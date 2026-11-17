/**
 * WindMapPage — the Sector Wind Map, under Research.
 *
 * How market-value share moves between S&P 500 sectors, sub-industries and
 * companies, and whether that "wind" has predicted later performance. This is
 * the quick version: the Market Casting prototype's self-contained page
 * (`frontend/public/research/wind-map.html`, a static snapshot with its data
 * baked in) shown in a sandboxed frame (see SandboxedReport). A full version
 * would serve the data from the API and render it with QuantUI's own components.
 */
import SandboxedReport from '../research/SandboxedReport';

/** Where the server serves the pre-built page. Listed in SANDBOXED_PAGES in csp.mjs. */
export const WIND_MAP_SRC = '/research/wind-map.html';

export default function WindMapPage() {
  return (
    <SandboxedReport
      title="Wind Map"
      src={WIND_MAP_SRC}
      frameTitle="Sector Wind Map"
      description={
        <>
          How market-value share moves between S&amp;P 500 sectors, sub-industries and companies,
          and how well that has predicted later returns. A prototype snapshot: the data date is
          shown in the map.
        </>
      }
    />
  );
}
