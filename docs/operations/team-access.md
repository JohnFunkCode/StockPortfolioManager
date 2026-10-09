# Team access runbook

How a team member gets access to the deployed system: the GCP roles they need, how a QuantUI user
is granted, and how each person mints the personal token their AI client uses against prod. This
material used to live in the readme; it moved here so the readme can stay a public tour.

Related runbooks:

- [`prod-jwt-tokens.md`](prod-jwt-tokens.md) — the HS256 service-token path in detail
- [`prod-promotion.md`](prod-promotion.md) — promoting a build from test to prod
- [`../architecture/quantui.md`](../architecture/quantui.md) — **granting a QuantUI user** (the
  "Granting a new user" paragraph is the single home for that procedure: the consent-screen
  Audience entry, the IAP accessor role, and an `owner_identities` row, the last two via
  `scripts/grant_quantui_iap_access.sh`. A UI-only user needs none of the roles below)

## GCP roles a team member needs

Minting a prod token and reaching the database through the Cloud SQL Auth Proxy need **four IAM
roles** in each project (prod and, for dev/CI work, test). A project owner grants them; a team
member cannot grant them to themselves.

| Role | On | Why |
|------|----|-----|
| `roles/secretmanager.secretAccessor` | the API's JWT signing secret | mint the prod MCP token (the mint script reads the signing secret and never prints it) |
| `roles/secretmanager.secretAccessor` | the project's database DSN secret | pull the DSN from Secret Manager (optional if the password was shared out-of-band) |
| `roles/cloudsql.client` | the project | authenticate the Cloud SQL Auth Proxy to the instance |
| `roles/browser` | the project | basic project visibility, so `gcloud projects describe` and Console browsing work |

> **Why `roles/browser` matters:** the three functional roles do **not** include
> `resourcemanager.projects.get`. Without `roles/browser`, `gcloud projects describe` and Console
> browsing fail with *"caller does not have permission"* even though token minting and the proxy
> work. It is a visibility role, not an access path — don't mistake that error for a broken grant.

**Use a real Google account.** IAM silently drops `user:` bindings for addresses that aren't backed
by an active Google account: the grant command reports success but the binding never persists. If
access isn't working and the owner confirms the grant went through, check that the address is a
real Google account and that it is the **active** one (below).

### Hitting a permissions issue?

1. **Check the active account first.** Most *"does not have permission"* errors are the wrong
   identity: `gcloud auth list` shows the active account, and `gcloud config set account <email>`
   switches it. The mint script and the proxy authenticate as whichever account is active.
2. **Confirm the account is a granted, real Google account** (see the caveat above).
3. **Still stuck? Ask a project owner** to confirm the bindings landed and re-grant if needed. Tell
   them which account and which project you are using.

## Minting your personal prod MCP token

The repo's `.mcp.json` points the seven remote MCP servers at prod, each sending
`Authorization: Bearer ${QUANTCORE_MCP_TOKEN}`. The wrappers forward that bearer unchanged to the
REST tier, whose `api/auth.py` accepts it on its HS256 service-token path. So the only thing each
person supplies is their own token. Without one, every data tool returns
`401: … Not enough segments` — while the wrapper-local `mcp_health_check` still passes, which is
misleading.

Prerequisite: `gcloud auth login` with an account holding the first role above.

Mint a **90-day** token and load it into the shell Claude Code launches from. The token is a live
bearer, so it is redirected straight to a file in `$HOME` and never printed:

```bash
# --sub is your owner partition (use your own name). 2160h = 90 days.
python scripts/mint_prod_jwt.py --output export --expires-hours 2160 --sub <you> > ~/.quantcore_mcp.env
chmod 600 ~/.quantcore_mcp.env
echo 'source ~/.quantcore_mcp.env' >> ~/.zshrc   # so every new shell inherits it
```

Then **restart Claude Code from a fresh shell**. `.mcp.json` reads `${QUANTCORE_MCP_TOKEN}` from
the process environment at startup, so a variable exported in a child shell never reaches an
already-running client. Verify with any prod data tool (e.g. `get_short_interest AAPL`): you should
get data, not a 401.

- **Rotation:** the token expires after **90 days**; re-run the mint command quarterly.
- `~/.quantcore_mcp.env` lives outside the repo and must **never** be committed.
- Each person mints their own with their own `--sub`. **Never share tokens.**

## Onboarding checklist

When onboarding someone, a project owner:

1. grants the four roles above in each project they need;
2. grants QuantUI access per [`quantui.md`](../architecture/quantui.md) (all three parts);
3. reminds them the MCP token expires after 90 days and should be rotated quarterly, with their own
   `--sub`.
