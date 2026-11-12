import Link from "next/link";

export const metadata = { title: "How Aerokym works" };

const REPO = "https://github.com/aryanpol73/aerokym";

export default function About() {
  return (
    <article className="prose">
      <p><Link href="/">← Back to the map</Link></p>
      <h1>How Aerokym works</h1>
      <p className="warn">
        <strong>Not for flight planning or any operational use.</strong> Aerokym is a research and
        education project. It gives no safety guidance and never says a route is smooth or safe.
        Use official aviation weather sources.
      </p>

      <h2>The idea</h2>
      <p>
        Every airliner broadcasts its position, altitude and vertical rate over ADS-B, and
        volunteers republish those signals for free. In rough air an aircraft that is trying to
        hold its altitude cannot quite do it: its vertical rate jitters. Aerokym looks for that
        jitter.
      </p>

      <h2>The method</h2>
      <ol>
        <li>Keep only level cruise: above FL200, within 300 ft of one altitude, away from climbs,
          descents and level-offs.</li>
        <li>In each 3-minute window, measure how much the reported vertical rate varies.</li>
        <li>Divide by the same aircraft&apos;s normal variation over its flight. This is the
          roughness index: 1.0 is normal for that aircraft.</li>
        <li>Group the windows into hexagons about 45 km across, by altitude band and time.</li>
      </ol>
      <table>
        <thead><tr><th>Index</th><th>Shown as</th></tr></thead>
        <tbody>
          <tr><td>below 1.22</td><td>Smooth</td></tr>
          <tr><td>1.22 to 1.60</td><td>Light</td></tr>
          <tr><td>1.60 and above</td><td>Moderate or greater</td></tr>
        </tbody>
      </table>
      <p>
        This is not the research-grade measure (RMSVA or EDR). Free public data has one sample
        about every 20 seconds, too coarse for those, so Aerokym uses a simpler index and says so.
      </p>

      <h2>How good is it?</h2>
      <p>
        On one day of US data (11 Nov 2026), compared with 429 pilot reports within 50 km, 1,000 ft
        and 10 minutes: when Aerokym said &quot;moderate or greater&quot; it agreed with the pilot
        51% of the time, and it caught 51% of the moderate-or-worse reports. Guessing
        &quot;rough&quot; every time would be right 33% of the time. So it carries real
        information and is far from precise.
      </p>
      <p>
        Those numbers come from a single day and are optimistic. Over India there are almost no
        pilot reports to check against, so results there are <strong>unvalidated</strong>.
      </p>

      <h2>Reading the map</h2>
      <ul>
        <li>Blue cells are places where aircraft flew and showed nothing unusual.</li>
        <li>Faint cells rest on one aircraft. Three or more agreeing aircraft give full strength.</li>
        <li>An empty area means no sampled aircraft, not smooth air.</li>
        <li>The map samples a small share of traffic, not all of it.</li>
      </ul>

      <h2>Data and licence</h2>
      <p>
        ADS-B data comes from <a href="https://adsb.lol">adsb.lol</a> under the{" "}
        <a href="https://opendatacommons.org/licenses/odbl/1-0/">ODbL v1.0</a>; cells and events
        published by Aerokym are share-alike under the same licence. Aircraft presence over India
        comes from the <a href="https://opensky-network.org">OpenSky Network</a>. Basemap ©{" "}
        <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors via
        OpenFreeMap. Code is MIT licensed: <a href={REPO}>source on GitHub</a>, with the full
        method in <a href={`${REPO}/blob/main/docs/method.md`}>docs/method.md</a>.
      </p>
    </article>
  );
}
