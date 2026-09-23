# Live-model descriptive study: proposals, outcomes and gate classification

Data collected 2026-09-20; audited 2026-09-22. Thirty seeds (0–29), four arms, 120 episodes. This is a descriptive study of a single hosted model. **No episode-level effect, equivalence or null is claimed at 30 seeds.** The existing live data are retained without relabelling or replacing failures. A corrected rerun was stopped because the configured Gemini key returned HTTP 402 (prepayment credits depleted); the user chose to finish with these existing data. **Historical global-budget enforcement and complete cost accounting remain unresolved limitations.** `paper/main.tex` is unchanged.

The model did not collapse to inactivity: 51.94–63.06% of first attempts produced a valid non-no-op action. Formatting failures are substantial and explicitly classified. On the recorded twin@1.0 corpus, 24 of 25 false positives at tau=0/theta=3 come from the threshold-definition mismatch; diagonal recall is 0.989 and FPR is 0.048. These describe the observed proposer and classifier, with the limitations below.

## Protocol and provenance

| Item | Recorded setting |
|---|---|
| Model / provider | gemini-3.6-flash / Google Generative Language API |
| Endpoint | https://generativelanguage.googleapis.com/v1beta |
| Temperature / prompt | 0.0 / v1; prompt SHA256 4b7ba907ef4dd5a249d611483947e29dc169df638c7d1fa6c0e605c67fe6d1be |
| Resolved model versions | Not retained in the historical logs; model identifier above is the requested identifier |
| Episode protocol | 120 ticks; decisions at 0,10,…,110; retry cap 2; horizon 20; tau=0; theta=3; gateway immune |
| Faults | Default FaultConfig: 3 faults; starts 20–260 (some fall beyond the episode) |
| LLM loop | 4 response steps; 20 s decision deadline; 25% reserved for finalization; 30 s provider ceiling |
| Provider request settings | Historical maximum 4 HTTP attempts per client call; default provider output limit |
| Parsing used for these data | Strict JSON; schema and semantic validation; original parser preserved in the artifact |
| A0 | Existing sweep seeds 0–29 reused; zero reject-all mismatches; no A0 episodes run |

Fidelity 1.0 is an established useful operating point for the rule controller (0.8 is also useful in the sweep). Fidelity 0.6 lies near the scripted controller's crossover, 0.549 [0.434, 0.677]. Queue simplification is off at both selected levels. These are preselected operating points, not an LLM crossover estimate.

## Calls, tokens, cost and cache

SHA256(UTF8(model + NUL + str(temperature) + NUL + json.dumps(messages, sort_keys=True, ensure_ascii=False))). Full messages include the prompt; provider URL, generation limits and resolved modelVersion are absent. Hits return stored replies without a provider call. Five separate cache files were preserved.

All five historical response caches are preserved. A cache hit incurred no new provider call. The original logs retain input and visible-output token counts but omit thinking-token usage and resolved modelVersion. They also count client calls rather than hidden HTTP retry attempts. Thus exact total tokens, HTTP request count and invoiced cost cannot be reconstructed.

| Measurement | Calls | Input tokens | Output tokens | USD |
|---|---|---|---|---|
| Before historical run (counters initialized) | 0 | 0 | 0 | 0 recorded |
| Successful historical provider responses | 5644 | 11498743 | 509258 | 10.533775 partial estimate |
| Response-cache hits | 2657 | 5189527 | 256524 | 0 incremental |
| After historical run: charged client calls | 5655 | see successful responses | thinking and failed-request usage unknown | actual total unavailable |

Charged client calls without a successful usage record: 11. The historical guard recorded 12,008,001 tokens; this excludes thinking. The input-plus-visible-output rate-card calculation is $10.533775. **Actual total cost and total billable tokens are not available.** The prior draft's claim that $10.53 was the actual billed total is withdrawn. The rates checked on 2026-09-22 are $0.75/M input and $3.75/M output including thinking. [Google pricing](https://ai.google.dev/gemini-api/docs/pricing).

The intended ceiling was 9,000 calls / 20M tokens, but the original two-seed pilot and four workers had separate guards totalling 17,000 calls / 38M tokens. The 5,655 recorded client calls and 12,008,001 recorded tokens are below the intended limits, but this does not establish compliance: one global guard was not enforced and thinking-token usage was omitted. No episodes stopped for budget exhaustion. The pilot's eight episodes are included once in the 120-episode cohort; no additional A0 was run. Future execution now uses persistent disjoint allocations whose sum is the global ceiling, complete returned token usage and conservative failed-request reservations. Those repairs were tested offline and do not retroactively repair historical compliance.

## 1. Decision outcome distribution (primary result)

An attempt means one call to the agent's decision routine, which may itself use up to four model responses. A gate rejection may trigger up to two more attempts at the same scheduled decision. The first-attempt table has exactly 360 observations per arm; the all-attempt table includes gate retries. The terminal-attempt table describes how each scheduled decision ends, including a produced action that the gate may still reject. These are distinct denominators; `decision_produced` means a valid non-no-op proposal, not necessarily an applied action.

### First attempt at each scheduled decision

| Outcome | LLM ungated (N=360) | LLM reject-all (N=360) | LLM twin@1.0 (N=360) | LLM twin@0.6 (N=360) |
|---|---|---|---|---|
| decision_produced | 227 (63.06%) | 187 (51.94%) | 190 (52.78%) | 189 (52.50%) |
| deliberate_no_op | 76 (21.11%) | 105 (29.17%) | 102 (28.33%) | 101 (28.06%) |
| tool_budget_exhausted | 1 (0.28%) | 2 (0.56%) | 0 (0.00%) | 0 (0.00%) |
| final_parse_failure | 53 (14.72%) | 64 (17.78%) | 67 (18.61%) | 69 (19.17%) |
| validation_exhausted | 2 (0.56%) | 0 (0.00%) | 1 (0.28%) | 0 (0.00%) |
| timeout | 1 (0.28%) | 2 (0.56%) | 0 (0.00%) | 1 (0.28%) |
| provider_budget_exhausted | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| provider_error | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |

### All attempts, including gate retries

| Outcome | LLM ungated (N=360) | LLM reject-all (N=660) | LLM twin@1.0 (N=550) | LLM twin@0.6 (N=540) |
|---|---|---|---|---|
| decision_produced | 227 (63.06%) | 394 (59.70%) | 249 (45.27%) | 245 (45.37%) |
| deliberate_no_op | 76 (21.11%) | 160 (24.24%) | 192 (34.91%) | 181 (33.52%) |
| tool_budget_exhausted | 1 (0.28%) | 2 (0.30%) | 1 (0.18%) | 0 (0.00%) |
| final_parse_failure | 53 (14.72%) | 92 (13.94%) | 106 (19.27%) | 106 (19.63%) |
| validation_exhausted | 2 (0.56%) | 7 (1.06%) | 1 (0.18%) | 2 (0.37%) |
| timeout | 1 (0.28%) | 5 (0.76%) | 1 (0.18%) | 6 (1.11%) |
| provider_budget_exhausted | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| provider_error | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |

### Terminal attempt at each scheduled decision

| Outcome | LLM ungated (N=360) | LLM reject-all (N=360) | LLM twin@1.0 (N=360) | LLM twin@0.6 (N=360) |
|---|---|---|---|---|
| decision_produced | 227 (63.06%) | 94 (26.11%) | 59 (16.39%) | 65 (18.06%) |
| deliberate_no_op | 76 (21.11%) | 160 (44.44%) | 192 (53.33%) | 181 (50.28%) |
| tool_budget_exhausted | 1 (0.28%) | 2 (0.56%) | 1 (0.28%) | 0 (0.00%) |
| final_parse_failure | 53 (14.72%) | 92 (25.56%) | 106 (29.44%) | 106 (29.44%) |
| validation_exhausted | 2 (0.56%) | 7 (1.94%) | 1 (0.28%) | 2 (0.56%) |
| timeout | 1 (0.28%) | 5 (1.39%) | 1 (0.28%) | 6 (1.67%) |
| provider_budget_exhausted | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| provider_error | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |

Valid non-no-op first proposals occur at 51.94–63.06% of scheduled decisions. The complete taxonomy above distinguishes intentional inactivity from failures. All failure categories use an explicit no-op fallback; none is silently counted as deliberate inactivity. Fallback counts over all attempts: {"ungated": 57, "always_reject": 106, "twin@1.0": 109, "twin@0.6": 114}.

## 2. Action-type mix

Counts and percentages below condition on valid non-no-op proposals and include gate retries. All five available non-no-op action types are shown, including zeros.

| Arm | N | scale_service | restart_service | migrate_service | throttle_service | reroute_traffic |
|---|---|---|---|---|---|---|
| LLM ungated | 227 | 90 (39.65%) | 72 (31.72%) | 65 (28.63%) | 0 (0.00%) | 0 (0.00%) |
| LLM reject-all | 394 | 151 (38.32%) | 30 (7.61%) | 211 (53.55%) | 2 (0.51%) | 0 (0.00%) |
| LLM twin@1.0 | 249 | 81 (32.53%) | 35 (14.06%) | 133 (53.41%) | 0 (0.00%) | 0 (0.00%) |
| LLM twin@0.6 | 245 | 73 (29.80%) | 36 (14.69%) | 135 (55.10%) | 1 (0.41%) | 0 (0.00%) |

The sweep references below use 150 seeds (0–149), so they are descriptive reference mixes, not paired controller comparisons. They exclude no-ops using the same candidate definition. `llm` in the sweep JSON denotes the deterministic scripted stub and made no model calls.

| Sweep controller | Arm | N | scale_service | restart_service | migrate_service | throttle_service | reroute_traffic |
|---|---|---|---|---|---|---|---|
| Rule | ungated | 204 | 166 (81.37%) | 13 (6.37%) | 24 (11.76%) | 1 (0.49%) | 0 (0.00%) |
| Rule | reject-all | 168 | 110 (65.48%) | 11 (6.55%) | 46 (27.38%) | 1 (0.60%) | 0 (0.00%) |
| Rule | twin@1.0 | 159 | 126 (79.25%) | 12 (7.55%) | 20 (12.58%) | 1 (0.63%) | 0 (0.00%) |
| Rule | twin@0.6 | 159 | 126 (79.25%) | 11 (6.92%) | 21 (13.21%) | 1 (0.63%) | 0 (0.00%) |
| Scripted | ungated | 1260 | 1260 (100.00%) | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| Scripted | reject-all | 1175 | 1175 (100.00%) | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| Scripted | twin@1.0 | 1167 | 1167 (100.00%) | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| Scripted | twin@0.6 | 1174 | 1174 (100.00%) | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |

Mix differences across arms combine trajectory changes, rejection feedback, retry weighting and response reuse. Ungated, the LLM proposes restarts or migrations in 60.35% of valid actions, compared with 18.13% for the rule controller and 0% for the scripted stub in the sweep. The reject-all feedback explicitly names that ablation. They do not isolate an effect of predictor fidelity on an otherwise fixed proposal population.

## 3. Candidate composition and magnitude

D = V20(action, then no-op) − V20(no-op), in service-SLO violation ticks. Violations are summed across services, so an episode total can exceed its 120 elapsed ticks. Beneficial means D<0, neutral D=0, harmful D>0. The classifier's positive ground-truth label is narrower: D>3. No-ops are excluded. Quantiles use the sweep's nearest-order-statistic convention. Available benefit is Σmax(−D,0); available harm is Σmax(D,0).

| Arm | Candidates | Beneficial | Neutral | Harmful D>0 (D>3) | Available benefit | Available harm | Beneficial magnitude min/median/p90/max | Harmful magnitude min/median/p90/max |
|---|---|---|---|---|---|---|---|---|
| LLM ungated | 227 | 55 | 25 | 147 (101) | 438 | 915 | 1 / 8 / 13 / 27 | 1 / 6 / 11 / 17 |
| LLM reject-all | 394 | 32 | 35 | 327 (292) | 252 | 2452 | 1 / 7 / 17 / 29 | 1 / 8 / 11 / 26 |
| LLM twin@1.0 | 249 | 12 | 23 | 214 (187) | 112 | 1614 | 1 / 8 / 17 / 29 | 1 / 7 / 11 / 27 |
| LLM twin@0.6 | 245 | 15 | 25 | 205 (180) | 146 | 1568 | 1 / 8 / 17 / 29 | 1 / 8 / 11 / 27 |

Neutral candidates have zero magnitude. Action-specific composition and beneficial-seed identities are retained in the JSON. Candidate sums include retries and overlapping 20-tick windows, so they cannot be added up as episode causal effects.

## 4. Preserved benefit, prevented harm and net ΣD

benefit_preserved = approved benefit / available benefit; harm_prevented = rejected harm / available harm. Net ΣD is the mean per seed of the sum of D over approved candidates. All intervals below are 95% seed-cluster percentile bootstrap intervals (5,000 resamples, shared seed indices across arms). A zero denominator is **not estimable (n/e)** and stored as null; zero-denominator bootstrap resamples are excluded and their estimable fraction is recorded. A boundary interval such as [1,1] describes this empirical sample, not perfect population performance.

| Arm | benefit_preserved [CI] (approved/available) | harm_prevented [CI] (rejected/available) | Net ΣD / seed [CI] | Net total |
|---|---|---|---|---|
| LLM ungated | 1.000 [1.000, 1.000] (438/438) | 0.000 [0.000, 0.000] (0/915) | 15.900 [11.967, 19.867] | 477.0 |
| LLM reject-all | 0.000 [0.000, 0.000] (0/252) | 1.000 [1.000, 1.000] (2452/2452) | 0.000 [0.000, 0.000] | 0.0 |
| LLM twin@1.0 | 1.000 [1.000, 1.000] (112/112) | 0.990 [0.980, 0.998] (1598/1614) | -3.200 [-6.767, -0.667] | -96.0 |
| LLM twin@0.6 | 0.603 [0.446, 0.933] (88/146) | 0.969 [0.943, 0.988] (1520/1568) | -1.333 [-3.533, 0.500] | -40.0 |

Each arm is evaluated on its own candidate set. These ratios describe its observed filtering, and are not a paired experiment on common proposals.

## 5. Classification at tau=0/theta=3 and tau=theta=3

Positive means reject; ground truth is D>theta. Definitional false positives are rejected candidates with tau<D≤theta. Predictive false positives are the remaining false positives. The definitional band is empty on the diagonal. The tau=3 rows relabel the fixed corpus collected at tau=0; they do not simulate trajectories under a tau=3 gate.

| Arm | tau / theta | TP / FN / FP / TN | Recall [CI] | FPR [CI] | Precision [CI] | FP definitional / predictive | FP on D<0 |
|---|---|---|---|---|---|---|---|
| LLM twin@1.0 | 0 / 3 | 185 / 2 / 25 / 37 | 0.989 [0.973, 1.000] | 0.403 [0.275, 0.545] | 0.881 [0.845, 0.919] | 24 / 1 | 0 |
| LLM twin@1.0 | 3 / 3 | 185 / 2 / 3 / 59 | 0.989 [0.973, 1.000] | 0.048 [0.000, 0.104] | 0.984 [0.965, 1.000] | 0 / 3 | 0 |
| LLM twin@0.6 | 0 / 3 | 175 / 5 / 21 / 44 | 0.972 [0.941, 0.995] | 0.323 [0.194, 0.472] | 0.893 [0.849, 0.933] | 16 / 5 | 4 |
| LLM twin@0.6 | 3 / 3 | 154 / 26 / 6 / 59 | 0.856 [0.797, 0.909] | 0.092 [0.033, 0.162] | 0.963 [0.934, 0.988] | 0 / 6 | 1 |

Ungated and reject-all have no predictive twin scores, so tau-dependent predictor metrics are not applicable. For completeness their fixed decision policies give the following theta=3 matrices; they are unchanged by tau and have no predictive-error interpretation.

| Control | TP / FN / FP / TN | Recall | FPR | Precision |
|---|---|---|---|---|
| LLM ungated | 0 / 101 / 0 / 126 | 0.000 | 0.000 | n/e |
| LLM reject-all | 292 / 0 / 102 / 0 | 1.000 | 1.000 | 0.741 |

## 6. Paired episode differences and power limitations

Difference = arm minus reject-all, paired by simulator seed. Negative values mean fewer observed violation ticks. Exact two-sided Wilcoxon signed-rank p-values enumerate the conditional sign distribution by dynamic programming, omit zero differences and use average ranks for ties. The signed-rank interpretation assumes symmetry/exchangeable signs under the null. Bootstrap intervals target the mean, whereas signed ranks test a different statistic; disagreement is not resolved by choosing the favorable result. Holm adjustment for the three requested comparisons is shown below; the four-comparison family including the exploratory fidelity contrast is retained in the JSON.

| Comparison | Mean | 95% t-CI | 95% bootstrap CI | Nonzero (lower/higher) | Exact Wilcoxon p | Holm p (3 primary) | Observed power (MCSE) | Normal heuristic n for 80% |
|---|---|---|---|---|---|---|---|---|
| ungated - reject-all | 24.733 | [16.739, 32.728] | [17.099, 32.001] | 28 (1/27) | 3.72529e-06 | 0.000011 | 1.000 (0.000) | 6 |
| twin@1.0 - reject-all | -4.300 | [-9.159, 0.559] | [-9.367, -0.500] | 12 (8/4) | 0.0546875 | 0.109375 | 0.481 (0.011) | 72 |
| twin@0.6 - reject-all | -1.300 | [-4.977, 2.377] | [-4.833, 2.134] | 14 (7/7) | 0.511597 | 0.511597 | 0.099 (0.007) | 451 |
| twin@1.0 - twin@0.6 | -3.000 | [-5.777, -0.223] | [-5.801, -0.667] | 10 (8/2) | 0.0273438 | n/e | 0.654 (0.011) | 49 |

Observed power is a post-hoc empirical resampling diagnostic: 2,000 samples of 30 paired differences are tested using the exact signed-rank calculation at unadjusted alpha=0.05. MCSE describes Monte Carlo error only. Required n uses ceil(((z0.975+z0.8)×SD/abs(mean))²), a paired-mean normal approximation; it is not the required size for the exact Wilcoxon test. Both quantities condition on this small observed sample and are unstable when differences are sparse. The gate-versus-reject-all comparisons at 30 seeds are underpowered; failure to reject is not evidence of no effect. Even a small p-value elsewhere in this table is retained only as a descriptive pilot statistic. **No episode-level effect is claimed.**

The twin@1.0 paired mean is sensitive to sparse opportunities: seed 6 contributes −63 of the total −129 ticks. Leaving that seed out changes the mean from −4.300 to −2.276; the primary estimate retains it. This sensitivity reinforces the limits of using 30 seeds for an episode-level conclusion.

## 7. Seeds containing a beneficial candidate

| Arm | Seeds with D<0 | Percentage | Seed identities |
|---|---|---|---|
| LLM ungated | 25 / 30 | 83.33% | 0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 18, 19, 20, 21, 22, 24, 25, 26, 28, 29 |
| LLM reject-all | 12 / 30 | 40.00% | 0, 2, 3, 4, 6, 8, 15, 16, 19, 22, 24, 26 |
| LLM twin@1.0 | 10 / 30 | 33.33% | 0, 4, 6, 15, 16, 19, 22, 24, 25, 26 |
| LLM twin@0.6 | 12 / 30 | 40.00% | 2, 3, 4, 6, 11, 15, 16, 19, 22, 24, 25, 26 |

These are opportunities on each arm's recorded proposal trajectory. In particular, reject-all's available local benefit is not an upper bound on a different policy's episode benefit.

## Engineering audit, corrections and remaining limitations

The original 120 episodes were reconstructed offline with zero simulator, counterfactual-label or gate-verdict mismatches. Its response-only cache could not faithfully replay 13 historical timeouts. The repaired runner records timing/error information for future runs; the old run remains unchanged. The original cache contained 273 unique responses that failed strict JSON parsing; 192 parsed when literal control characters were allowed. That is a response-level diagnostic, not 192 failed decisions recovered. Changing parsing can change subsequent actions and states, so historical outcomes were not relabelled as if those actions had happened.

Engineering fixes validated offline include preserved thinking-token metadata, accounting for failed-request uncertainty, persistent study-wide budget allocation, atomic cache writes, explicit first/terminal/all-attempt denominators, all eight outcome categories including zeros, strict cohort/label checks, exact-test empirical power, and an HTTP billing/configuration circuit breaker. The report removes unsupported episode claims and incorrect sweep comparisons. Parser and accounting repairs were not used to manufacture replacement outcomes for the historical data.

- One model, one prompt, one response-cache realization; no generalization to all LLMs or sampling variability.
- Seed pairing controls simulator fault schedules, not independent model sampling; shared response cache induces reuse across arms.
- Candidate metrics use each arm's own trajectory and include retry attempts. Feedback explicitly identifies the reject-all ablation, so differences combine state, feedback and retries.
- Twenty-tick local counterfactuals use no-op continuations and overlap in time; summed D is neither episode causal effect nor a recoverable-benefit ceiling.
- Tau=3 is fixed-corpus relabelling of proposals collected at tau=0, not a tau=3 deployment.
- Zero denominators are not estimable; [1,1] empirical ratio intervals at a sample boundary do not establish population perfection.
- Post-hoc power and normal required-n figures are unstable pilot diagnostics, not evidence for a null, efficacy, or a guaranteed future sample size.
- The historical run lost thinking-token usage and timing/error cache data. The $10.53 visible-token estimate is not a verified actual charge, and the intended single global budget was not enforced. These cannot be repaired retrospectively.

The model/prompt/temperature are fixed, but the results characterize the original cached controller configuration, not independent fresh sampling at every arm or decision. Parser failures are a property of the model-plus-parser system and should not be described as the model lacking useful actions. Literal-newline tolerance can recover some formatting failures, but evaluating the changed controller requires a new live run. The aborted 2026-09-22 attempt is excluded from every primary table because it encountered a systemic provider billing failure.

## Reproduction and artifacts

### Aborted correction attempt: separate usage disclosure

Before: 0 new calls, 0 returned tokens, $0 recorded. After: 185 guarded client calls, 0 successful new responses, 0 returned provider tokens, $0 in metered response charges. 2,409,861 tokens were conservatively reserved for failures. Unresolved client calls when workers stopped: 1 (11,837 pending reserved tokens). A separate read-only model-list request verified model availability. The guarded diagnostic generation returned HTTP 402 RESOURCE_EXHAUSTED, explicitly stating prepayment credits were depleted. No actual invoice total was available. These attempts produced no new usable live-model responses and do not enter the primary study. After this diagnosis the user selected the existing-data analysis; no further generation calls were made.

The [JSON alongside this report](llm_descriptive_study.json) contains exact counts, per-seed totals, all metric denominators, estimable bootstrap fractions, the full threshold grid, and audit metadata. The [primary archive](llm_descriptive_study_artifacts.zip) and [artifact manifest](llm_descriptive_study_artifact_manifest.json) provide portable evidence and SHA256 hashes. The artifact archive preserves shards, response caches, chronological call journals, prompt/source snapshots and hashes. Offline replay makes no provider calls and reuses A0 from the sweep.

```powershell
Expand-Archive -LiteralPath docs/research/llm_descriptive_study_artifacts.zip -DestinationPath results/llm_descriptive_study -Force
Expand-Archive -LiteralPath docs/research/llm_descriptive_study_billing_attempt.zip -DestinationPath results/llm_descriptive_study -Force
.\.venv\Scripts\python.exe scripts\llm_descriptive_study.py analyze --shards results\llm_descriptive_study\source\shards --calls-log-root results\llm_descriptive_study\source --price-in 0.75 --price-out 3.75 --replay-audit results\llm_descriptive_study\historical_replay_audit.json --output docs\research\llm_descriptive_study.json --markdown docs\research\llm_descriptive_study.md
.\.venv\Scripts\python.exe scripts\llm_study_replay.py --historical
```

A future paid run uses a new output directory and `run --workers 4 --worker <0..3> --max-calls 9000 --max-tokens 20000000`; the ceilings are global totals divided across workers, not ceilings to multiply by four. Existing completed shards are immutable and resumes retain the same budget allocation.

Replay audit is embedded in the JSON and distributed with the artifact archive.
