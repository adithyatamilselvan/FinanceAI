export type User = {
  id: string; email: string; name: string; currency: string; monthly_income: number;
  current_savings: number | null; savings_target_pct: number | null;
};
export type TxType = "income" | "expense" | "refund" | "transfer";
export type Tx = {
  id: string; date: string; description: string; amount: number; type: TxType; category: string; notes: string | null;
  source: string; import_id: string | null; currency: string; similar_updated?: number;
  slip?: { file: string; payment_method: string | null; reference: string | null; provider: string | null; status: string | null; ocr_confidence: number | null; verified: boolean };
};
export type Category = { name: string; type: "expense" | "income"; fixed: boolean; essential: boolean; custom: boolean };
export type Totals = { income: number; gross_expense: number; refunds: number; net_expense: number; net_cash_flow: number; savings_pct: number | null; transaction_count: number };
export type Comparison = { income_change: number; expense_change: number; savings_change: number; expense_change_pct: number | null; income_change_pct: number | null } | null;
export type BudgetRow = { category: string; budget: number; actual: number; remaining: number; utilization_pct: number | null; over_budget: boolean; unbudgeted: boolean };
export type BudgetPerf = { period: string; exists: boolean; total_budget: number; total_actual: number; remaining: number; utilization_pct: number | null; rows: BudgetRow[]; overspent_categories: string[] };
export type Health = { score: number | null; components: { name: string; score: number; max: number; detail: string }[]; note: string };
export type Dashboard = {
  period: string; has_transactions: boolean; currency: string; totals: Totals;
  categories: { category: string; amount: number; pct: number | null; fixed: boolean }[];
  fixed_variable: { fixed: number; variable: number; fixed_pct: number | null };
  vs_previous_month: Comparison; vs_same_month_last_year: Comparison;
  trend: { period: string; income: number; net_expense: number; net_cash_flow: number }[];
  health_score: Health; budget: BudgetPerf;
};
export type Goal = {
  id: string; name: string; target_amount: number; current_amount: number; remaining: number; progress_pct: number | null; target_date: string | null;
  monthly_contribution: number | null; months_remaining: number | null; required_monthly_contribution: number | null;
  estimated_completion: string | null; on_track: boolean | null; achieved: boolean; assumptions: string[];
};
export type ImportRow = { row: number; date: string; description: string; amount: number; type: TxType; category: string; status: "new" | "duplicate" };
export type ImportPreview = {
  needs_mapping: boolean; import_id: string | null; headers: string[]; suggested_mapping?: Record<string, string>; mapping?: Record<string, string | boolean>;
  sample_rows?: string[][];
  summary?: { valid_rows: number; new: number; duplicates: number; errors: number; income_total: number; expense_total: number; refund_total: number; transfers: number };
  already_imported?: boolean; rows?: ImportRow[]; rows_truncated?: boolean; errors?: { row: number; reason: string }[];
};
export type Recurring = {
  id: string; name: string; category: string | null; amount: number; frequency: string; status: string; confidence: number; estimated_monthly: number;
  next_expected: string | null; evidence: { occurrences: number; explanation: string; dates: string[] } | null;
};

export type SlipFields = {
  date: string | null; time: string | null; amount: number | null; currency: string | null; type: "income" | "expense" | "transfer" | "refund" | null;
  merchant: string | null; sender: string | null; payment_method: string | null; reference: string | null; provider: string | null;
  status: "successful" | "pending" | "failed" | "refunded" | "unknown" | null; description: string | null;
};
export type SlipItem = {
  id: string; file: string; ok: boolean; error: string | null; method: string | null; pages: number | null; ocr_confidence: number | null; fields: SlipFields;
  category: string | null; uncertain: Record<string, string>; notes: Record<string, string>; missing: string[]; absent: string[]; review_reasons: string[]; review_required: boolean;
  verified: boolean; edited: string[]; duplicate: { kind: string; level: string; detail: string } | null; import_status: string; import_status_label: string;
  default_selected: boolean; transaction_id: string | null;
};
export type SlipSummary = {
  total_files: number; processed: number; failed: number; transactions: number; requiring_review: number; possible_duplicates: number; ready: number; imported: number;
  successful_expense_paise: number; successful_income_paise: number;
};
export type SlipBatch = { id: string; status: "open" | "importing" | "imported"; expires_at: string; items: SlipItem[]; summary: SlipSummary; result: { imported: number; skipped_duplicates: number; not_selected: number } | null };
export type SlipStatus = { available: boolean; engine: string | null; reason: string | null; limits: { max_file_bytes: number; max_files_per_batch: number; max_pdf_pages: number; allowed_extensions: string[]; batch_expiry_hours: number; low_confidence_below: number } };
