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
 */
import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { useChatOptional } from './ChatContext';
import { INTERACTION_REGISTRY, validateInteractionPayload } from './componentRegistry';
import type { ChatDirective } from './types';

export interface DirectiveInteractionApi {
  /** True when gestures from this instance can reach the conversation. */
  enabled: boolean;
  interact: (action: string, payload: Record<string, unknown>, text?: string) => void;
}

const INERT: DirectiveInteractionApi = { enabled: false, interact: () => undefined };

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

  const api = useMemo<DirectiveInteractionApi>(() => {
    const actions = INTERACTION_REGISTRY[component];
    if (!chat || !componentId || !actions) return INERT;
    return {
      enabled: true,
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
  }, [chat, component, componentId, props]);

  return <Ctx.Provider value={api}>{children}</Ctx.Provider>;
}
