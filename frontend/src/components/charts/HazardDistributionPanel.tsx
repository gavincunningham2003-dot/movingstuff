import type { HazardDistribution } from '../../api/types';
import { hazardClassColor } from '../../lib/hazardColors';
import { humanize } from '../../lib/format';
import { distributionRows } from '../../lib/summaryStats';
import { Card } from '../ui/Card';
import { EmptyState } from '../ui/EmptyState';
import { DistributionBarChart } from './DistributionBarChart';

interface HazardDistributionPanelProps {
  distribution: HazardDistribution;
  total?: number;
  /** Max classes per hazard chart (others are grouped into "Other"). */
  maxClasses?: number;
}

const HAZARD_TITLES: Record<string, string> = {
  wildfire_fhsz: 'Wildfire — Fire Hazard Severity Zone',
  flood_nfhl: 'Flood — NFIP flood zone',
  tsunami: 'Tsunami inundation zone',
  earthquake_pga: 'Earthquake — PGA band',
  fire_perimeters: 'Historic fire perimeters',
};

export function HazardDistributionPanel({ distribution, total, maxClasses = 8 }: HazardDistributionPanelProps) {
  const hazards = Object.keys(distribution);
  if (hazards.length === 0) {
    return (
      <EmptyState
        title="No hazard distribution"
        message="The API returned no hazard distribution. The ingestion pipeline has not assessed any properties yet."
      />
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {hazards.map((hazardId) => {
        let rows = distributionRows(distribution[hazardId]);
        if (rows.length > maxClasses) {
          const head = rows.slice(0, maxClasses - 1);
          const rest = rows.slice(maxClasses - 1).reduce((a, r) => a + r.count, 0);
          rows = [...head, { label: 'Other', count: rest }];
        }
        const n = rows.reduce((a, r) => a + r.count, 0);
        return (
          <Card
            key={hazardId}
            title={HAZARD_TITLES[hazardId] ?? humanize(hazardId)}
            subtitle={`${hazardId} · ${n.toLocaleString('en-US')} classified`}
          >
            {rows.length === 0 ? (
              <EmptyState compact title="No classes" message="No properties were classified for this hazard." />
            ) : (
              <DistributionBarChart
                data={rows}
                total={total}
                colorFor={(label) => hazardClassColor(hazardId, label)}
                height={Math.max(140, 28 * rows.length + 40)}
              />
            )}
          </Card>
        );
      })}
    </div>
  );
}
