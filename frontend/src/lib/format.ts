import type { Source } from "./api";

/** Rupees: paise precision for tiny model costs, grouped thousands for yearly figures. */
export function inr(value: number): string {
  if (value === 0) return "₹0";
  if (value > 0 && value < 0.01) return `₹${value.toPrecision(2)}`;
  const digits = value >= 100 ? 0 : 2;
  return `₹${value.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export const pct = (value: number) => `${value.toFixed(1)}%`;

export function ms(value: number | null | undefined): string {
  if (value == null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;
}

export const SOURCE_LABEL: Record<Source, string> = {
  rules: "Rules engine",
  local: "Local model",
  small: "Small model",
  large: "Large model",
  cache: "Cache",
};
