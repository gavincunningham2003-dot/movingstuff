import type { PointAssessmentOut, HazardAssessmentOut } from '../../api/types';
import { fmtNum, fmtScore } from '../../lib/format';

const STATUS_LABEL: Record<HazardAssessmentOut['coverage_status'], string> = {
  covered: 'in hazard area',
  not_in_hazard_area: 'not in hazard area',
  outside_coverage: 'no data here',
  unavailable: 'unavailable',
};

function hazardLine(h: HazardAssessmentOut): string {
  if (h.coverage_status === 'covered') {
    const parts: string[] = [];
    if (h.classification) parts.push(h.classification);
    if (h.value !== null && h.value !== undefined && h.units && h.units !== 'in zone (1)') {
      parts.push(`${fmtNum(h.value, h.units === 'g' ? 3 : 1)} ${h.units}`);
    }
    return parts.join(' · ') || STATUS_LABEL.covered;
  }
  return STATUS_LABEL[h.coverage_status];
}

const SHORT: Record<string, string> = {
  wildfire_fhsz: 'Fire hazard zone',
  wildfire_history: 'Fires nearby since 1990',
  wildfire_ml: 'Fire exposure model',
  earthquake_pga: 'Shaking, 2% in 50 yr',
  earthquake_pga_10pc50: 'Shaking, 10% in 50 yr',
  earthquake_fault_proximity: 'Nearest fault',
  earthquake_history: 'M4+ quakes, 50 km',
  liquefaction: 'Liquefaction zone',
  landslide: 'Landslide susceptibility',
  flood_nfhl: 'Flood zone',
  tsunami: 'Tsunami zone',
  extreme_rainfall: '100-yr 24-h rain',
  windstorm: 'Windstorm (county)',
  drought: 'Drought (county)',
  extreme_heat: 'Extreme heat (county)',
  coastal_flooding: 'Coastal flood (county)',
  winter_storm: 'Winter storm (county)',
};

const ORDER = [
  'wildfire_fhsz',
  'wildfire_history',
  'wildfire_ml',
  'earthquake_pga',
  'earthquake_pga_10pc50',
  'earthquake_fault_proximity',
  'earthquake_history',
  'liquefaction',
  'landslide',
  'flood_nfhl',
  'tsunami',
  'extreme_rainfall',
];

export function PointAssessmentCard({
  lon,
  lat,
  data,
  isLoading,
  error,
}: {
  lon: number;
  lat: number;
  data: PointAssessmentOut | undefined;
  isLoading: boolean;
  error: Error | null;
}) {
  const head = (
    <div className="text-sm font-semibold text-slate-100">
      {data?.county ? `${data.county} County` : 'Selected location'}
      <span className="ml-2 font-normal text-slate-400">
        {lat.toFixed(4)}, {lon.toFixed(4)}
      </span>
    </div>
  );
  if (isLoading) {
    return (
      <div className="min-w-[260px] rounded-md bg-slate-900 p-3 text-xs text-slate-200">
        {head}
        <div className="mt-2 text-slate-400">Assessing all hazards for this spot…</div>
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="min-w-[260px] rounded-md bg-slate-900 p-3 text-xs text-slate-200">
        {head}
        <div className="mt-2 text-red-300">{error?.message ?? 'Assessment failed.'}</div>
      </div>
    );
  }
  const byId = new Map(data.hazards.map((h) => [h.hazard_id, h]));
  const ordered = [
    ...ORDER.map((id) => byId.get(id)).filter((h): h is HazardAssessmentOut => Boolean(h)),
    ...data.hazards.filter((h) => !ORDER.includes(h.hazard_id) && h.method !== 'regional_index'),
  ];
  const p = data.prediction?.probability;
  return (
    <div className="min-w-[300px] max-w-[360px] rounded-md bg-slate-900 p-3 text-xs text-slate-200">
      {head}
      {!data.in_california && (
        <div className="mt-1 text-amber-300">Outside California: hazard layers do not cover this point.</div>
      )}
      <div className="mt-2 flex items-baseline justify-between rounded border border-slate-700 bg-slate-800/60 px-2 py-1">
        <span className="text-slate-400">Screening index</span>
        <span className="text-base font-semibold tabular text-teal-300">{fmtScore(data.score.score)}</span>
      </div>
      <div className="text-[11px] text-slate-500">{data.score.label}</div>
      {p !== null && p !== undefined && (
        <div className="mt-2 flex items-baseline justify-between">
          <span className="text-slate-400">Fire perimeter exposure, next year</span>
          <span className="tabular">
            {(p * 100).toFixed(2)}%
            {data.prediction?.quality && (
              <span className="ml-2 text-[10px] uppercase tracking-wide text-slate-500">
                inputs {data.prediction.quality}
                {data.prediction.feature_completeness !== null && data.prediction.feature_completeness !== undefined
                  ? ` (${Math.round(data.prediction.feature_completeness * 100)}%)`
                  : ''}
              </span>
            )}
          </span>
        </div>
      )}
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        {ordered.map((h) => (
          <div key={h.hazard_id} className="contents">
            <dt className="truncate text-slate-400" title={h.hazard_name}>
              {SHORT[h.hazard_id] ?? h.hazard_name}
            </dt>
            <dd className={`text-right ${h.coverage_status === 'covered' ? 'text-slate-100' : 'text-slate-500'}`}>
              {hazardLine(h)}
            </dd>
          </div>
        ))}
      </dl>
      <div className="mt-2 text-[10px] leading-snug text-slate-500">
        Screening index is a weighted summary with assumed weights, not a probability. Fire exposure is a calibrated
        model probability of lying inside a mapped perimeter next year.
      </div>
    </div>
  );
}
