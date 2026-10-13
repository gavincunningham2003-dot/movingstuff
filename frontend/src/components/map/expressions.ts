import type { ExpressionSpecification } from 'maplibre-gl';

import type { LegendEntry } from '../../api/types';
import { COLORS } from '../../lib/hazardColors';

/**
 * Build a MapLibre expression from loosely typed parts. The style-spec tuple
 * types are too strict to assemble dynamically, so this is the single cast
 * point for all data-driven styling.
 */
export function expr(...parts: unknown[]): ExpressionSpecification {
  return parts as unknown as ExpressionSpecification;
}

export type PointColorBy = 'composite_score' | 'wildfire_fhsz' | 'flood_zone' | 'pga_2pc50_g';

export const POINT_COLOR_OPTIONS: { value: PointColorBy; label: string }[] = [
  { value: 'composite_score', label: 'Screening index (0-100)' },
  { value: 'wildfire_fhsz', label: 'Wildfire FHSZ class' },
  { value: 'flood_zone', label: 'Flood zone (NFHL)' },
  { value: 'pga_2pc50_g', label: 'PGA, 2% in 50 yr (g)' },
];

/** Legend entries matching `pointColorExpression`. */
export function pointLegend(colorBy: PointColorBy): { label: string; color: string }[] {
  switch (colorBy) {
    case 'wildfire_fhsz':
      return [
        { label: 'Very High', color: COLORS.wildfireVeryHigh },
        { label: 'High', color: COLORS.wildfireHigh },
        { label: 'Moderate', color: COLORS.wildfireModerate },
        { label: 'Not in FHSZ / unknown', color: COLORS.none },
      ];
    case 'flood_zone':
      return [
        { label: 'SFHA (A*, V*)', color: COLORS.floodSfha },
        { label: 'X shaded (0.2% annual chance)', color: COLORS.floodXShaded },
        { label: 'X unshaded / minimal', color: COLORS.floodX },
        { label: 'Unknown', color: COLORS.none },
      ];
    case 'pga_2pc50_g':
      return [
        { label: '≥ 0.8 g', color: '#7f1d1d' },
        { label: '0.6 – 0.8 g', color: COLORS.wildfireVeryHigh },
        { label: '0.4 – 0.6 g', color: COLORS.wildfireHigh },
        { label: '0.2 – 0.4 g', color: COLORS.wildfireModerate },
        { label: '< 0.2 g', color: '#86efac' },
        { label: 'Unknown', color: COLORS.none },
      ];
    case 'composite_score':
    default:
      return [
        { label: '75 – 100', color: COLORS.wildfireVeryHigh },
        { label: '50 – 75', color: COLORS.wildfireHigh },
        { label: '25 – 50', color: COLORS.wildfireModerate },
        { label: '0 – 25', color: COLORS.teal },
        { label: 'No score', color: COLORS.none },
      ];
  }
}

function numericStep(prop: string, stops: (number | string)[], fallback: string): ExpressionSpecification {
  return expr(
    'case',
    expr('==', expr('typeof', expr('get', prop)), 'number'),
    expr('step', expr('get', prop), ...stops),
    fallback,
  );
}

/** circle-color expression for unclustered property points. */
export function pointColorExpression(colorBy: PointColorBy): ExpressionSpecification {
  switch (colorBy) {
    case 'wildfire_fhsz':
      return expr(
        'match',
        expr('get', 'wildfire_fhsz'),
        ['Very High', 'very high', 'VERY HIGH', 'Very_High', 'very_high'],
        COLORS.wildfireVeryHigh,
        ['High', 'high', 'HIGH'],
        COLORS.wildfireHigh,
        ['Moderate', 'moderate', 'MODERATE', 'Medium', 'medium'],
        COLORS.wildfireModerate,
        COLORS.none,
      );
    case 'flood_zone':
      // SFHA = zones starting with A or V. Shaded X carries an explicit label.
      return expr(
        'case',
        expr('!=', expr('typeof', expr('get', 'flood_zone')), 'string'),
        COLORS.none,
        expr('in', expr('slice', expr('upcase', expr('get', 'flood_zone')), 0, 1), expr('literal', ['A', 'V'])),
        COLORS.floodSfha,
        expr('in', 'SHADED', expr('upcase', expr('get', 'flood_zone'))),
        COLORS.floodXShaded,
        expr('in', '0.2', expr('get', 'flood_zone')),
        COLORS.floodXShaded,
        expr('==', expr('slice', expr('upcase', expr('get', 'flood_zone')), 0, 1), 'X'),
        COLORS.floodX,
        COLORS.none,
      );
    case 'pga_2pc50_g':
      return numericStep(
        'pga_2pc50_g',
        ['#86efac', 0.2, COLORS.wildfireModerate, 0.4, COLORS.wildfireHigh, 0.6, COLORS.wildfireVeryHigh, 0.8, '#7f1d1d'],
        COLORS.none,
      );
    case 'composite_score':
    default:
      return numericStep(
        'composite_score',
        [COLORS.teal, 25, COLORS.wildfireModerate, 50, COLORS.wildfireHigh, 75, COLORS.wildfireVeryHigh],
        COLORS.none,
      );
  }
}

/**
 * fill-color for a hazard overlay. Uses `properties.color` when the backend
 * provides it, otherwise matches `properties.value` / `class` / `classification`
 * / `label` against the legend entries, falling back to `fallback`.
 */
export function legendFillExpression(legend: LegendEntry[], fallback: string): ExpressionSpecification {
  const matchArgs: unknown[] = [];
  const seen = new Set<string>();
  for (const entry of legend) {
    const key = String(entry.value ?? entry.label ?? '');
    if (!key || seen.has(key) || !entry.color) continue;
    seen.add(key);
    matchArgs.push(key, entry.color);
  }
  const classValue = expr(
    'to-string',
    expr('coalesce', expr('get', 'value'), expr('get', 'class'), expr('get', 'classification'), expr('get', 'label'), ''),
  );
  const byLegend = matchArgs.length ? expr('match', classValue, ...matchArgs, fallback) : fallback;
  return expr('case', expr('==', expr('typeof', expr('get', 'color')), 'string'), expr('get', 'color'), byLegend);
}
