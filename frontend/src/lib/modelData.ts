/**
 * Helpers that normalise the loosely-typed `dict[str, Any]` parts of the ML
 * metrics contract into shapes the charts can consume. Each helper accepts a
 * few plausible key spellings so the UI degrades gracefully.
 */

import type {
  FeatureImportanceEntry,
  GeographicPerformanceEntry,
  ModelMetricsOut,
  PrPoint,
  RocPoint,
  ShapSummaryEntry,
} from '../api/types';
import { isNum } from './format';

type Row = Record<string, unknown>;

export function pickNumber(row: Row, keys: string[]): number | null {
  for (const k of keys) {
    const v = row[k];
    if (isNum(v)) return v;
    if (typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v))) return Number(v);
  }
  return null;
}

export function pickString(row: Row, keys: string[]): string | null {
  for (const k of keys) {
    const v = row[k];
    if (typeof v === 'string' && v.trim() !== '') return v;
    if (isNum(v)) return String(v);
  }
  return null;
}

export function rocPoints(model: ModelMetricsOut): RocPoint[] {
  return model.roc_curve
    .map((r) => ({ fpr: pickNumber(r, ['fpr', 'x']), tpr: pickNumber(r, ['tpr', 'y']) }))
    .filter((p): p is RocPoint => p.fpr !== null && p.tpr !== null)
    .sort((a, b) => a.fpr - b.fpr);
}

export function prPoints(model: ModelMetricsOut): PrPoint[] {
  return model.pr_curve
    .map((r) => ({
      recall: pickNumber(r, ['recall', 'x']),
      precision: pickNumber(r, ['precision', 'y']),
    }))
    .filter((p): p is PrPoint => p.recall !== null && p.precision !== null)
    .sort((a, b) => a.recall - b.recall);
}

export interface CalibrationBin {
  predicted: number;
  observed: number;
  n: number | null;
}

export function calibrationBins(model: ModelMetricsOut): CalibrationBin[] {
  return model.calibration
    .map((r) => ({
      predicted: pickNumber(r, ['mean_predicted', 'prob_pred', 'predicted', 'x', 'bin_mean']),
      observed: pickNumber(r, ['fraction_positive', 'fraction_positives', 'prob_true', 'observed', 'y']),
      n: pickNumber(r, ['n', 'count', 'n_samples']),
    }))
    .filter((p): p is CalibrationBin => p.predicted !== null && p.observed !== null)
    .sort((a, b) => a.predicted - b.predicted);
}

export function featureImportance(model: ModelMetricsOut): FeatureImportanceEntry[] {
  return model.feature_importance
    .map((r) => ({
      feature: pickString(r, ['feature', 'name']) ?? '',
      importance: pickNumber(r, ['importance', 'gain', 'value', 'weight']),
    }))
    .filter((p): p is FeatureImportanceEntry => p.feature !== '' && p.importance !== null)
    .sort((a, b) => b.importance - a.importance);
}

export function shapSummary(model: ModelMetricsOut): ShapSummaryEntry[] {
  return model.shap_summary
    .map((r) => ({
      feature: pickString(r, ['feature', 'name']) ?? '',
      mean_abs_shap: pickNumber(r, ['mean_abs_shap', 'mean_abs', 'importance', 'value']),
    }))
    .filter((p): p is ShapSummaryEntry => p.feature !== '' && p.mean_abs_shap !== null)
    .sort((a, b) => b.mean_abs_shap - a.mean_abs_shap);
}

export function geographicPerformance(model: ModelMetricsOut): GeographicPerformanceEntry[] {
  return model.geographic_performance
    .map((r) => ({
      region: pickString(r, ['region', 'county', 'name', 'area']) ?? '',
      n: pickNumber(r, ['n', 'count', 'n_samples']) ?? 0,
      prevalence: pickNumber(r, ['prevalence', 'positive_rate']),
      roc_auc: pickNumber(r, ['roc_auc', 'auc']),
      pr_auc: pickNumber(r, ['pr_auc', 'average_precision']),
    }))
    .filter((p) => p.region !== '');
}

export interface ConfusionCells {
  tp: number | null;
  fp: number | null;
  fn: number | null;
  tn: number | null;
}

export function confusionCells(model: ModelMetricsOut): ConfusionCells {
  const c = model.confusion as Row;
  return {
    tp: pickNumber(c, ['tp', 'true_positive', 'true_positives']),
    fp: pickNumber(c, ['fp', 'false_positive', 'false_positives']),
    fn: pickNumber(c, ['fn', 'false_negative', 'false_negatives']),
    tn: pickNumber(c, ['tn', 'true_negative', 'true_negatives']),
  };
}

/** Well-known metric keys, in display order, with labels. */
export const METRIC_LABELS: { key: string; label: string; alt?: string[] }[] = [
  { key: 'roc_auc', label: 'ROC-AUC', alt: ['auc'] },
  { key: 'pr_auc', label: 'PR-AUC', alt: ['average_precision', 'ap'] },
  { key: 'brier', label: 'Brier', alt: ['brier_score'] },
  { key: 'log_loss', label: 'Log loss', alt: ['logloss'] },
  { key: 'precision', label: 'Precision' },
  { key: 'recall', label: 'Recall' },
  { key: 'f1', label: 'F1', alt: ['f1_score'] },
  { key: 'accuracy', label: 'Accuracy' },
  { key: 'balanced_accuracy', label: 'Balanced acc.' },
];

export function metricValue(metrics: Record<string, number | null>, key: string, alt: string[] = []): number | null {
  for (const k of [key, ...alt]) {
    const v = metrics[k];
    if (isNum(v)) return v;
  }
  return null;
}

/** Keys present in `metrics` that are not covered by METRIC_LABELS. */
export function extraMetricKeys(models: ModelMetricsOut[]): string[] {
  const known = new Set(METRIC_LABELS.flatMap((m) => [m.key, ...(m.alt ?? [])]));
  const extra = new Set<string>();
  for (const m of models) {
    for (const k of Object.keys(m.metrics)) if (!known.has(k)) extra.add(k);
  }
  return [...extra].sort();
}
