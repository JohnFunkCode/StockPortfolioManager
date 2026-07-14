/**
 * The bridge between a rendered directive component and the chat backchannel.
 *
 * DirectiveRenderer wraps each rendered component in a provider carrying that
 * instance's identity (componentId, component name, validated props). Cards
 * call `interact(action, payload, text?)`:
 *
 *   - 'context'-mode actions queue onto the composer (they ride along with
 *     the user's next typed message);
 *   - 'message'-mode actions submit `text` immediately with the interaction
 *     attached.
 *
 * The mode comes from INTERACTION_REGISTRY, payloads are validated before
 * anything leaves the component, and everything is inert (enabled: false)
 * when there is no chat context, no instance id, or no vocabulary for the
 * component — so cards render safely anywhere.
 *
 * Once a gesture from an instance has been SENT, the instance is `locked`:
 * it is part of the conversational record the model already answered, so its
 * mark and affordances must never change again. Locked cards receive the
 * `consumed` gestures to render their frozen state from.
 */
import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { useChatOptional } from './ChatContext';
import { INTERACTION_REGISTRY, validateInteractionPayload } from './componentRegistry';
import type { ChatDirective, ChatInteraction } from './types';

export interface DirectiveInteractionApi {
  /** True when gestures from this instance can reach the conversation. */
  enabled: boolean;
  /** True once a gesture from this instance has been sent — immutable now. */
  locked: boolean;
  /** The gestures that were sent, oldest first — locked cards render these. */
  consumed: ChatInteraction[];
  interact: (action: string, payload: Record<string, unknown>, text?: string) => void;
}

const INERT: DirectiveInteractionApi = {
  enabled: false,
  locked: false,
  consumed: [],
  interact: () => undefined,
};

const Ctx = createContext<DirectiveInteractionApi>(INERT);

export function useDirectiveInteractions(): DirectiveInteractionApi {
  return useContext(Ctx);
}

interface ProviderProps {
  directive: ChatDirective;
  /** Normalized props actually passed to the component (ticker uppercased). */
  props: Record<string, unknown>;
  children: ReactNode;
}

export function DirectiveInteractionProvider({ directive, props, children }: ProviderProps) {
  const chat = useChatOptional();
  const { component, componentId } = directive;
  const consumed: ChatInteraction[] =
    (componentId && chat?.consumedInteractions?.[componentId]) || [];

  const api = useMemo<DirectiveInteractionApi>(() => {
    const actions = INTERACTION_REGISTRY[component];
    if (!chat || !componentId || !actions) return INERT;
    if (consumed.length > 0) {
      return { enabled: false, locked: true, consumed, interact: () => undefined };
    }
    return {
      enabled: true,
      locked: false,
      consumed: [],
      interact: (action, payload, text) => {
        const spec = actions[action];
        if (!spec) return;
        if (!validateInteractionPayload(component, action, payload).ok) return;
        const interaction = {
          component_id: componentId,
          component,
          action,
          payload,
          props,
        };
        if (spec.mode === 'message' && text) {
          void chat.sendMessage(text, [interaction]);
        } else {
          chat.queueInteraction(interaction);
        }
      },
    };
  }, [chat, component, componentId, props, consumed]);

  return <Ctx.Provider value={api}>{children}</Ctx.Provider>;
}
