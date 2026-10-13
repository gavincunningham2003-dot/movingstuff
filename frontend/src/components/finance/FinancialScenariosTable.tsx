import { useState } from 'react';

import type { FinancialScenarioOut } from '../../api/types';
import { fmtAny, fmtFracPct, fmtUsd } from '../../lib/format';
import { AssumptionKindBadge, Badge } from '../ui/Badge';
import { CaveatList } from '../ui/CaveatBanner';
import { EmptyState } from '../ui/EmptyState';

interface FinancialScenariosTableProps {
  scenarios: FinancialScenarioOut[];
}

export function FinancialScenariosTable({ scenarios }: FinancialScenariosTableProps) {
  const [open, setOpen] = useState<string | null>(null);

  if (!scenarios.length) {
    return (
      <EmptyState
        compact
        title="No financial scenarios"
        message="The API returned no scenarios. Scenarios require an exposed value and a hazard assessment with coverage."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-max border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-left text-xs font-semibold uppercase tracking-wide text-slate-400">
            <th className="px-3 py-2">Scenario</th>
            <th className="px-3 py-2">Hazard</th>
            <th className="px-3 py-2">Description</th>
            <th className="px-3 py-2 text-right">Damage ratio</th>
            <th className="px-3 py-2 text-right">Exposed value</th>
            <th className="px-3 py-2 text-right">Gross loss</th>
            <th className="px-3 py-2 text-right">Deductible</th>
            <th className="px-3 py-2 text-right">Limit</th>
            <th className="px-3 py-2 text-right">Net insured loss</th>
            <th className="px-3 py-2 text-right">Assumptions</th>
          </tr>
        </thead>
        <tbody>
          {scenarios.map((s) => {
            const isOpen = open === s.scenario_id;
            return (
              <ScenarioRows key={s.scenario_id} scenario={s} isOpen={isOpen} onToggle={() => setOpen(isOpen ? null : s.scenario_id)} />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ScenarioRows({
  scenario: s,
  isOpen,
  onToggle,
}: {
  scenario: FinancialScenarioOut;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr className="border-b border-slate-800/70">
        <td className="px-3 py-2 font-mono text-xs text-slate-300">{s.scenario_id}</td>
        <td className="px-3 py-2">
          <Badge>{s.hazard_id}</Badge>
        </td>
        <td className="max-w-xs px-3 py-2 text-slate-300">{s.description}</td>
        <td className="tabular px-3 py-2 text-right">{fmtFracPct(s.damage_ratio)}</td>
        <td className="tabular px-3 py-2 text-right">{fmtUsd(s.exposed_value)}</td>
        <td className="tabular px-3 py-2 text-right font-semibold text-amber-200">{fmtUsd(s.gross_loss)}</td>
        <td className="tabular px-3 py-2 text-right">{fmtUsd(s.deductible_applied)}</td>
        <td className="tabular px-3 py-2 text-right">{fmtUsd(s.limit_applied)}</td>
        <td className="tabular px-3 py-2 text-right font-semibold text-teal-200">{fmtUsd(s.net_insured_loss)}</td>
        <td className="px-3 py-2 text-right">
          <button type="button" onClick={onToggle} className="text-xs text-teal-300 hover:underline">
            {s.assumptions.length} {isOpen ? '▲' : '▼'}
          </button>
        </td>
      </tr>
      {isOpen && (
        <tr className="border-b border-slate-800/70 bg-slate-950/50">
          <td colSpan={10} className="px-3 py-3">
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Assumptions</div>
                {s.assumptions.length === 0 ? (
                  <div className="text-xs text-slate-500">None recorded.</div>
                ) : (
                  <ul className="space-y-1 text-sm">
                    {s.assumptions.map((a, i) => (
                      <li key={`${a.name}-${i}`} className="flex flex-wrap items-center gap-2">
                        <span className="text-slate-300">{a.name}</span>
                        <span className="tabular text-slate-100">{fmtAny(a.value)}</span>
                        <AssumptionKindBadge kind={a.kind} />
                        {a.source && <span className="text-xs text-slate-500">{a.source}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div>
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Caveats</div>
                {s.caveats.length === 0 ? (
                  <div className="text-xs text-slate-500">None.</div>
                ) : (
                  <CaveatList items={s.caveats} />
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
