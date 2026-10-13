import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import { fmtInt, fmtPct } from '../../lib/format';
import { AXIS_LINE, AXIS_TICK, CURSOR_FILL, GRID_STROKE, TOOLTIP_ITEM_STYLE, TOOLTIP_LABEL_STYLE, TOOLTIP_STYLE } from './chartTheme';

export interface DistributionDatum {
  label: string;
  count: number;
}

interface DistributionBarChartProps {
  data: DistributionDatum[];
  colorFor?: (label: string) => string;
  /** Horizontal bars (category on the y axis). Default true. */
  horizontal?: boolean;
  height?: number;
  total?: number;
  labelWidth?: number;
}

export function DistributionBarChart({
  data,
  colorFor,
  horizontal = true,
  height = 220,
  total,
  labelWidth = 120,
}: DistributionBarChartProps) {
  const sum = total ?? data.reduce((a, d) => a + d.count, 0);
  const color = (label: string) => (colorFor ? colorFor(label) : '#2dd4bf');

  if (horizontal) {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 4 }}>
          <CartesianGrid stroke={GRID_STROKE} horizontal={false} />
          <XAxis type="number" tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} tickFormatter={(v) => fmtInt(Number(v))} />
          <YAxis type="category" dataKey="label" width={labelWidth} tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} />
          <Tooltip
            cursor={CURSOR_FILL}
            contentStyle={TOOLTIP_STYLE}
            labelStyle={TOOLTIP_LABEL_STYLE}
            itemStyle={TOOLTIP_ITEM_STYLE}
            formatter={(value) => {
              const n = Number(value);
              return [`${fmtInt(n)} (${fmtPct(sum ? (n / sum) * 100 : null)})`, 'Properties'];
            }}
          />
          <Bar dataKey="count" radius={[0, 3, 3, 0]} isAnimationActive={false}>
            {data.map((d) => (
              <Cell key={d.label} fill={color(d.label)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 4 }}>
        <CartesianGrid stroke={GRID_STROKE} vertical={false} />
        <XAxis dataKey="label" tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} interval={0} angle={-30} textAnchor="end" height={60} />
        <YAxis tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} tickFormatter={(v) => fmtInt(Number(v))} />
        <Tooltip
          cursor={CURSOR_FILL}
          contentStyle={TOOLTIP_STYLE}
          labelStyle={TOOLTIP_LABEL_STYLE}
          itemStyle={TOOLTIP_ITEM_STYLE}
          formatter={(value) => {
            const n = Number(value);
            return [`${fmtInt(n)} (${fmtPct(sum ? (n / sum) * 100 : null)})`, 'Properties'];
          }}
        />
        <Bar dataKey="count" radius={[3, 3, 0, 0]} isAnimationActive={false}>
          {data.map((d) => (
            <Cell key={d.label} fill={color(d.label)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
