"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { type Accuracy, fetchAccuracy } from "@/lib/api";

const pct = (v: number | null) => (v === null ? "n/a" : `${Math.round(v * 100)}%`);
// Below this many scored reports, a percentage is noise and is not shown large.
const MIN_REPORTS = 100;

export default function AccuracyPage() {
  const [data, setData] = useState<Accuracy | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    fetchAccuracy(30, ctrl.signal).then(setData).catch((e) => {
      if (e.name !== "AbortError") setError(true);
    });
    return () => ctrl.abort();
  }, []);

  if (error)
    return (
      <article className="prose">
        <p><Link href="/">← Back to the map</Link></p>
        <h1>Accuracy</h1>
        <p className="warn">Cannot reach the Aerokym API.</p>
      </article>
    );
  if (!data) return <article className="prose"><p>Loading…</p></article>;

  const { pooled, runs, calibration: cal } = data;
  const enough = pooled.n_matched >= MIN_REPORTS;

  return (
    <article className="prose">
      <p><Link href="/">← Back to the map</Link></p>
      <h1>Accuracy</h1>
      <p>
        Aerokym checks itself against pilot reports of turbulence every day and publishes the
        result here, good or bad. Method version <code>{data.meta.method_version}</code>.
      </p>

      <h2>Independent validation, last 30 days</h2>
      {pooled.n_matched === 0 ? (
        <p className="warn">
          <strong>No independent validation yet.</strong> No day has had both detections and
          pilot reports close enough to compare. Until it does, the only evidence is the
          one-day calibration below.
        </p>
      ) : (
        <>
          {!enough && (
            <p className="warn">
              <strong>Too few reports to draw conclusions:</strong> {pooled.n_matched} scored so
              far, and at least {MIN_REPORTS} are needed before these percentages mean much.
            </p>
          )}
          <div className="tiles">
            <div className="tile">
              <span className="big">{pct(pooled.precision)}</span>
              <span>of our &quot;moderate or greater&quot; calls agreed with the pilot</span>
              <span className="muted">{pooled.tp} of {pooled.tp + pooled.fp} calls</span>
            </div>
            <div className="tile">
              <span className="big">{pct(pooled.recall)}</span>
              <span>of moderate-or-worse reports were caught</span>
              <span className="muted">{pooled.tp} of {pooled.tp + pooled.fn} reports</span>
            </div>
            <div className="tile">
              <span className="big">{pct(pooled.naive_precision)}</span>
              <span>would agree if we called everything rough (the baseline to beat)</span>
              <span className="muted">{pooled.n_moderate_plus} of {pooled.n_matched} reports</span>
            </div>
          </div>
          <p className="hint">
            {pooled.n_matched} pilot reports scored over {pooled.days}{" "}
            {pooled.days === 1 ? "day" : "days"}, out of {pooled.n_pireps} cruise-level turbulence
            reports in the region. The rest had no sampled aircraft nearby.
          </p>
        </>
      )}

      {runs.length > 0 && (
        <>
          <h2>Day by day</h2>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>Date</th><th>Region</th><th>Reports</th><th>Scored</th>
                  <th>Hits</th><th>False alarms</th><th>Misses</th><th>Aircraft</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.date + r.region}>
                    <td>{r.date}</td><td>{r.region}</td><td>{r.n_pireps}</td>
                    <td>{r.n_matched}</td><td>{r.tp}</td><td>{r.fp}</td><td>{r.fn}</td>
                    <td>{r.n_aircraft}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">
            A hit: we said moderate or greater and so did the pilot. A false alarm: we did, the
            pilot said less. A miss: the pilot did, we said less.
          </p>
        </>
      )}

      <h2>Calibration (not independent)</h2>
      <p>
        The thresholds were set on one day, {cal.date}, over the {cal.region}: {cal.n_matched}{" "}
        pilot reports matched within {cal.match}. Scored on the half of those reports not used
        for fitting ({cal.held_out_reports}):
      </p>
      <table>
        <tbody>
          <tr><td>Calls that agreed with the pilot</td><td>{pct(cal.precision)}</td></tr>
          <tr><td>Moderate-or-worse reports caught</td><td>{pct(cal.recall)}</td></tr>
          <tr><td>Baseline: call everything rough</td><td>{pct(cal.naive_precision)}</td></tr>
          <tr>
            <td>Chance a rough report outscores a smooth one</td>
            <td>{pct(cal.auc)} (95% interval {pct(cal.auc_interval[0])} to{" "}
              {pct(cal.auc_interval[1])})</td>
          </tr>
        </tbody>
      </table>
      <p className="hint">{cal.caveat}</p>

      <h2>How to read this</h2>
      <ul>{data.caveats.map((c) => <li key={c}>{c}</li>)}</ul>
      <p>
        A report is scored by the mean roughness index of every sampled aircraft within{" "}
        {cal.match} of it, and counts as &quot;moderate or greater&quot; at an index of{" "}
        {data.threshold_index.toFixed(2)} or more. Full method:{" "}
        <a href="https://github.com/aryanpol73/aerokym/blob/main/docs/method.md">docs/method.md</a>.
      </p>
    </article>
  );
}
