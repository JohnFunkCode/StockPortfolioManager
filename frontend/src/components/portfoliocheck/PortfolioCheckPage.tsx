/**
 * PortfolioCheckPage — the quarterly Portfolio Check, under Research.
 *
 * Runs each holding through the Wind Map's tested industry rules (sell / watch /
 * hold / buy), adjusted for position size and taxes. This is a DEMO: the
 * Market Casting prototype's report for a made-up sample portfolio
 * (`frontend/public/research/portfolio-check-sample.html`), shown in a
 * sandboxed frame (see SandboxedReport). It never shows the viewer's own
 * holdings — a static page is the same for every user and is committed to the
 * repo, so it must only ever hold made-up data. The per-user version, built
 * from the viewer's own lots, is docs/proposals/portfolio-check-plan.md.
 */
import { Alert } from '@mui/material';
import SandboxedReport from '../research/SandboxedReport';

/** Where the server serves the pre-built sample. Listed in SANDBOXED_PAGES in csp.mjs. */
export const PORTFOLIO_CHECK_SRC = '/research/portfolio-check-sample.html';

export default function PortfolioCheckPage() {
  return (
    <SandboxedReport
      title="Portfolio Check"
      src={PORTFOLIO_CHECK_SRC}
      frameTitle="Portfolio Check sample report"
      description={
        <>
          A quarterly check of each holding against the Wind Map&apos;s industry rules, adjusted for
          position size and taxes. Research output, not financial advice.
        </>
      }
    >
      <Alert severity="info" sx={{ mb: 2 }}>
        Sample portfolio: this demo report is for made-up holdings, not your portfolio. A version
        built from your own lots is planned.
      </Alert>
    </SandboxedReport>
  );
}
