import { useMemo, useState } from 'react';

import type { ConfusionAt, ModelMetricsOut, SweepPoint } from '../../api/types';
import { fmtFracPct, fmtInt, fmtNum } from '../../lib/format';

type Split = 'test' | 'spatial_test';

function Cell({ label, value, tone }: { label: string; value: number | null; tone: string }) {
  return (
    <div className={`rounded-md border px-3 py-3 text-center ${tone}`}>
      <div className="text-[11px] uppercase tracking-wide opacity-80">{label}</div>
      <div className="tabular text-xl font-semibold">{fmtInt(value)}</div>
    </div>
  );
}

function Matrix({ c }: { c: ConfusionAt }) {
  return (
    <div className="grid grid-cols-[auto_1fr_1fr] gap-2 text-xs">
      <div />
      <div className="text-center text-slate-400">Predicted positive</div>
      <div className="text-center text-slate-400">Predicted negative</div>
      <div className="flex items-center text-slate-400">Actual positive</div>
      <Cell label="True positive" value={c.tp} tone="border-teal-500/40 bg-teal-500/10 text-teal-200" />
      <Cell label="False negative" value={c.fn} tone="border-red-500/40 bg-red-500/10 text-red-200" />
      <div className="flex items-center text-slate-400">Actual negative</div>
      <Cell label="False positive" value={c.fp} tone="border-amber-500/40 bg-amber-500/10 text-amber-200" />
      <Cell label="True negative" value={c.tn} tone="border-slate-700 bg-slate-800/60 text-slate-200" />
    </div>
  );
}

function fromLegacy(model: ModelMetricsOut): ConfusionAt | null {
  const c = model.confusion;
  if (!c || c.tp === undefined) return null;
  const n = c.tp + c.fp + c.fn + c.tn;
  return {
    threshold: model.threshold,
    tp: c.tp,
    fp: c.fp,
    fn: c.fn,
    tn: c.tn,
    flagged_share: n ? (c.tp + c.fp) / n : 0,
    precision: c.tp + c.fp ? c.tp / (c.tp + c.fp) : null,
    recall: c.tp + c.fn ? c.tp / (c.tp + c.fn) : null,
    specificity: c.tn + c.fp ? c.tn / (c.tn + c.fp) : null,
  };
}

/**
 * Confusion matrix at a chosen operating point. The threshold stored with the model is
 * only one choice (F1-optimal on validation); screening users typically prefer "flag the
 * riskiest N %" or "reach a target recall". Named points come from training; the slider
 * walks the flagged-share sweep the backend evaluated on the same split.
 */
export function OperatingPointPanel({ model }: { model: ModelMetricsOut }) {
  const points = model.operating_points ?? [];
  const sweeps = model.threshold_sweep ?? {};
  const hasSpatial = points.some((p) => p.spatial_test) || Boolean(sweeps.spatial_test?.length);
  const [split, setSplit] = useState<Split>('test');
  const [mode, setMode] = useState<string>(
    points.some((p) => p.name === 'top_5pct') ? 'top_5pct' : points.length ? points[0].name : 'stored',
  );
  const [sweepIdx, setSweepIdx] = useState<number>(() => {
    const s = sweeps.test ?? [];
    const i = s.findIndex((x) => x.target_share >= 0.05);
    return i >= 0 ? i : Math.max(0, s.length - 1);
  });

  const sweep: SweepPoint[] = sweeps[split] ?? [];
  const current: ConfusionAt | null = useMemo(() => {
    if (mode === 'sweep') return sweep[Math.min(sweepIdx, Math.max(0, sweep.length - 1))] ?? null;
    const op = points.find((p) => p.name === mode);
    if (op) return (split === 'spatial_test' ? op.spatial_test : op.test) ?? op.test;
    return fromLegacy(model);
  }, [mode, sweep, sweepIdx, points, split, model]);

  if (!current) {
    return <div className="text-xs text-slate-500">No confusion counts were returned for this model.</div>;
  }
  const positives = current.tp + current.fn;
  const flagged = current.tp + current.fp;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-slate-400">Operating point</span>
        {points.map((p) => (
          <button
            key={p.name}
            type="button"
            onClick={() => setMode(p.name)}
            className={`rounded border px-2 py-1 ${
              mode === p.name
                ? 'border-teal-500/60 bg-teal-500/10 text-teal-200'
                : 'border-slate-700 bg-slate-800/60 text-slate-300 hover:border-slate-500'
            }`}
          >
            {p.label}
          </button>
        ))}
        {sweep.length > 0 && (
          <button
            type="button"
            onClick={() => setMode('sweep')}
            className={`rounded border px-2 py-1 ${
              mode === 'sweep'
                ? 'border-teal-500/60 bg-teal-500/10 text-teal-200'
                : 'border-slate-700 bg-slate-800/60 text-slate-300 hover:border-slate-500'
            }`}
          >
            Custom share
          </button>
        )}
        {hasSpatial && (
          <span className="ml-auto inline-flex overflow-hidden rounded border border-slate-700">
            {(['test', 'spatial_test'] as Split[]).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setSplit(s)}
                className={`px-2 py-1 ${split === s ? 'bg-slate-700 text-slate-100' : 'bg-slate-900 text-slate-400'}`}
              >
                {s === 'test' ? 'Later years' : 'Unseen regions'}
              </button>
            ))}
          </span>
        )}
      </div>

      {mode === 'sweep' && sweep.length > 0 && (
        <div className="mb-3 flex items-center gap-3 text-xs text-slate-400">
          <span className="whitespace-nowrap">Flag the riskiest</span>
          <input
            type="range"
            min={0}
            max={sweep.length - 1}
            value={Math.min(sweepIdx, sweep.length - 1)}
            onChange={(e) => setSweepIdx(Number(e.target.value))}
            className="w-full accent-teal-400"
          />
          <span className="tabular whitespace-nowrap text-slate-200">{fmtFracPct(current.flagged_share)}</span>
        </div>
      )}

      <Matrix c={current} />

      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-400 sm:grid-cols-3">
        <span>Threshold <span className="tabular text-slate-200">{fmtNum(current.threshold, 4)}</span></span>
        <span>Flagged <span className="tabular text-slate-200">{fmtInt(flagged)}</span> ({fmtFracPct(current.flagged_share)})</span>
        <span>Positives caught <span className="tabular text-slate-200">{fmtInt(current.tp)} of {fmtInt(positives)}</span></span>
        <span>Precision <span className="tabular text-slate-200">{fmtFracPct(current.precision)}</span></span>
        <span>
          Recall <span className="tabular text-slate-200">{fmtFracPct(current.recall)}</span>
          {current.recall_ci95 && (
            <span className="text-slate-500"> (95% CI {fmtFracPct(current.recall_ci95[0])}–{fmtFracPct(current.recall_ci95[1])})</span>
          )}
        </span>
        <span>
          Lift vs random{' '}
          <span className="tabular text-slate-200">
            {current.lift !== null && current.lift !== undefined ? `${fmtNum(current.lift, 1)}x` : '—'}
          </span>
        </span>
      </div>
      <p className="mt-2 text-[11px] leading-snug text-slate-500">
        {current.kind === 'ranking_budget'
          ? 'Review budget: the riskiest share of THIS evaluation sample by model rank. It is a budget, not a transferable probability threshold. '
          : 'Fixed threshold: a probability cut chosen on the validation years and applied unchanged to this sample. '}
        For a rare event, recall rises
        only by flagging a larger share of locations; the lift column says how much better than random the flagged
        set is.
      </p>
    </div>
  );
}
