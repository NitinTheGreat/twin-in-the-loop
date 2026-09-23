import json
import re
import runpy
import shutil
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE.parent
ROOT = HERE.parents[2]
DRAFT = ROOT / "paper/draft_2026-09-23"
OLD = (ROOT / "paper/main.tex").read_text(encoding="utf-8")
SWEEP = json.loads((ROOT / "docs/research/extended_fidelity_sweep.json").read_text(encoding="utf-8"))
MUT = json.loads((ROOT / "docs/research/test_mutation_results.json").read_text(encoding="utf-8"))
TEST_RUN = json.loads((HERE / "test_run.json").read_text(encoding="utf-8"))

runpy.run_path(str(DRAFT / "gen_numbers.py"), run_name="__main__")
macros = dict(re.findall(r"\\newcommand\{\\([A-Za-z]+)\}\{(.*)\}$", (DRAFT / "numbers.tex").read_text(encoding="utf-8"), re.M))


def between(text, start, end, include_end=True):
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j + len(end)] if include_end else text[i:j]


def block_ending(text, anchor, begin, end):
    k = text.index(anchor)
    i = text.rindex(begin, 0, k)
    j = text.index(end, k)
    return text[i:j + len(end)]


def f3(x, sign=False):
    s = f"{x:+.3f}" if sign else f"{x:.3f}"
    return s.replace("-", "\\ensuremath{-}").replace("+", "\\ensuremath{+}")


macros["TestsPassed"] = str(TEST_RUN["passed"])
killed = [m for m in MUT["mutations"] if m["status"] == "killed"]
macros["MutationsKilled"] = str(len(killed))
macros["MutationsTotal"] = str(len(MUT["mutations"]))

intro = between(OLD, "\\textbf{Background: autonomous operation", "\\textbf{Contributions.}", include_end=False).rstrip()
old_twin = "re-prompted on failure~\\cite{processplant}."
assert intro.count(old_twin) == 1
intro = intro.replace(old_twin, "re-prompted on failure~\\cite{processplant}, and infrastructure-orchestration work treats the twin as a mandatory gate between an optimisation agent and the execution layer~\\cite{twingatedorch}.")

related = between(OLD, "\\textbf{Digital twins for network change validation.}", "\\begin{table*}[t]", include_end=False).rstrip()

rel_table = block_ending(OLD, "\\caption{Representative prior works", "\\begin{table*}", "\\end{table*}")
this_row = [l for l in rel_table.splitlines() if l.startswith("\\textbf{This work}")][0]
new_rows = ("Orchestration twin gate~\\cite{twingatedorch} & Compute-infrastructure orchestration & Twin as mandatory gate with re-optimisation & Constraint satisfaction of candidates & Same gate-plus-retry loop; no counterfactual or reject-all control \\\\\n"
            "\\textbf{This work} & Edge / IoT network (simulated) & Fault-blind twin gate, 5 fidelity axes, 6 levels & Counterfactual label for every proposal; reject-all, random and schedule-aware controls; exact tests at "
            + macros["NSeeds"] + " seeds & -- \\\\")
rel_table = rel_table.replace(this_row, new_rows)

methods = between(OLD, "\\subsection{System Architecture}", "\\end{algorithm}")
methods = methods.replace("dash/.style=", "dasharrow/.style=").replace("\\draw[dash]", "\\draw[dasharrow]")
assert "[dash]" not in methods
for old, new in (
        ("\\node[box, right=1.2cm of tel, xshift=2.2cm] (sse) {SSE server\\\\+ dashboard};",
         "\\node[box, right=1.2cm of ex] (sse) {SSE server\\\\+ dashboard};"),
        ("\\draw[dasharrow] (ex.south) to[bend left=20] node[right, font=\\scriptsize]{events} (sse.north);",
         "\\draw[dasharrow] (ex.east) -- node[above, font=\\scriptsize]{events} (sse.west);"),
        ("\\draw[arr] (tel) -- node[right, font=\\scriptsize]{observation} (ag);",
         "\\draw[arr] (tel) -- node[left, font=\\scriptsize]{observation} (ag);"),
        ("node[above, font=\\scriptsize, pos=0.4]{reject + reason} (ag.north east);",
         "node[left, font=\\scriptsize, pos=0.7]{reject + reason} (ag.north east);")):
    assert methods.count(old) == 1, old
    methods = methods.replace(old, new)
old_ctrl = re.search(r"\\textbf\{Controllers\.\}.*?\n", methods).group(0)
methods = methods.replace(old_ctrl, (
    "\\textbf{Controllers.} \\emph{Null} always emits \\texttt{no\\_op}. \\emph{Rule} is a threshold controller that, "
    "for example, migrates a service off an edge node whose utilisation stays above 90\\% while the service is in "
    "violation, restarts a service under memory pressure, and scales services in p95 violation when host capacity "
    "allows. \\emph{Scripted} is a deterministic aggressive scale-or-hold policy that stands in for an aggressive "
    "proposer and makes no model calls. An \\emph{LLM agent} built on LangGraph, with telemetry tools, a bounded "
    "tool-use loop, response caching and a hard budget guard, is used as a further proposer in a descriptive study.\n"))

tests = block_ending(OLD, "\\caption{Functional test cases", "\\begin{table*}", "\\end{table*}")
tests = re.sub(r"Regression suite & .*?\\\\", f"Regression suite & \\\\texttt{{pytest -W error}} & All pass & {TEST_RUN['passed']}/{TEST_RUN['passed']} \\\\\\\\", tests)
tests = re.sub(r"Mutation check & .*?\\\\", f"Mutation check & {len(MUT['mutations'])} critical mutations & All detected & {len(killed)}/{len(MUT['mutations'])} killed \\\\\\\\", tests)

cond = SWEEP["conditions"]["discrete_switch"]["agents"]
GATES = [("Ungated", "ungated"), ("Reject-all", "always_reject"), ("Random", "random"),
         ("Schedule-aware", "oracle"), ("Twin@0.0", "twin@0.0"), ("Twin@0.6", "twin@0.6"), ("Twin@1.0", "twin@1.0")]
gate_rows = []
for label, arm in GATES:
    cells = [label]
    for agent in ("rule", "llm"):
        a = cond[agent]["arms"][arm]
        net = a["net_sum_d_approved"]
        lo, hi = net["bootstrap_ci95"]
        cells += [f"{a['episode_mean_violation_ticks']:.1f}", f3(a["benefit_preserved"]["point"]),
                  f3(a["harm_prevented"]["point"]),
                  f"{f3(net['mean_per_seed'], True)} [{f3(lo, True)}, {f3(hi, True)}]"]
    gate_rows.append(" & ".join(cells) + " \\\\")

ep_rows = []
for ctrl, prefix in (("Rule", "Rule"), ("Scripted", "Scr")):
    for arm, lv in (("ungated", None), ("twin@0.0", "Zero"), ("twin@0.8", "Eight"), ("twin@1.0", "Ten")):
        if lv is None:
            p = f"{prefix}UngatedEp"
            cells = [f"\\{p}I", f"\\{p}CII", f"\\{p}PI", f"\\{p}HolmI", f"\\{p}NZI{{}} (\\{p}BetterI/\\{p}WorseI)"]
        else:
            p = f"{prefix}Ep"
            cells = [f"\\{p}{lv}I", f"\\{p}CI{lv}I", f"\\{p}P{lv}I", f"\\{p}Holm{lv}I",
                     f"\\{p}NZ{lv}I{{}} (\\{p}Better{lv}I/\\{p}Worse{lv}I)"]
        ep_rows.append(f"{ctrl} & {arm} & " + " & ".join(cells) + " \\\\")

exp_rows = []
for lv, name in zip(["0.0", "0.2", "0.4", "0.6", "0.8", "1.0"], ["Zero", "Two", "Four", "Six", "Eight", "Ten"]):
    exp_rows.append(f"{lv} & \\RuleNet{name}I & \\RuleEp{name}I & \\ScrNet{name}I & \\ScrEp{name}I \\\\")

src = (HERE / "main_src.tex").read_text(encoding="utf-8")
for key, value in {"%%INTRO%%": intro, "%%RELATED%%": related, "%%RELTABLE%%": rel_table,
                   "%%METHODS_CORE%%": methods, "%%TESTTABLE%%": tests, "%%GATEROWS%%": "\n".join(gate_rows),
                   "%%EPROWS%%": "\n".join(ep_rows), "%%EXPROWS%%": "\n".join(exp_rows)}.items():
    assert src.count(key) == 1, key
    src = src.replace(key, value)

missing = set()


def expand(match):
    name = match.group(1)
    if name in macros:
        return macros[name]
    return match.group(0)


for _ in range(3):
    src = re.sub(r"\\([A-Za-z]+)(?:\{\})?", expand, src)

old_bib = between(OLD, "\\begin{thebibliography}", "\\end{thebibliography}")
items = dict(re.findall(r"\\bibitem\{([^}]+)\}\s*(.*?)(?=\n\s*\\bibitem|\n\s*\\end\{thebibliography\})", old_bib, re.S))
items.update({
    "twingatedorch": "``Digital-twin validation gate between an optimisation agent and the execution layer in infrastructure orchestration,'' arXiv:2602.10900 and arXiv:2604.09705, 2026 (bibliographic details to be verified).",
    "kleinrock": "L.~Kleinrock, \\emph{Queueing Systems, Volume 1: Theory}. New York, NY, USA: Wiley, 1975.",
    "wilcoxon": "F.~Wilcoxon, ``Individual comparisons by ranking methods,'' \\emph{Biometrics Bulletin}, vol.~1, no.~6, pp.~80--83, 1945.",
    "holm": "S.~Holm, ``A simple sequentially rejective multiple test procedure,'' \\emph{Scandinavian Journal of Statistics}, vol.~6, no.~2, pp.~65--70, 1979.",
})
order = []
for group in re.findall(r"\\cite\{([^}]+)\}", src):
    for key in group.split(","):
        key = key.strip()
        if key not in order:
            order.append(key)
unknown = [k for k in order if k not in items]
if unknown:
    raise SystemExit(f"uncited or unknown references: {unknown}")
bib = "\\begin{thebibliography}{99}\n\n" + "\n\n".join(f"\\bibitem{{{k}}} {items[k].strip()}" for k in order) + "\n\n\\end{thebibliography}"
bib = re.sub(r"Available: (https?://\S+?)(\.?)$", lambda m: "Available: \\url{" + m.group(1) + "}" + m.group(2), bib, flags=re.M)
src = src.replace("%%BIBLIOGRAPHY%%", bib)

template = (HERE / "main_src.tex").read_text(encoding="utf-8")
known = {"NetSD", "Sigma", "IEEEauthorblockN", "IEEEauthorblockA", "IEEEoverridecommandlockouts"}
leftover = sorted(set(re.findall(r"\\([A-Z][A-Za-z]+)", template)) - set(macros) - known)
if leftover:
    raise SystemExit(f"unexpanded macros: {leftover}")
if re.search(r"[^\x00-\x7f]", src):
    raise SystemExit("non-ASCII characters present")

abstract = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", src, re.S).group(1)
words = len(re.sub(r"\\[a-zA-Z]+|[{}$\\]", " ", abstract).split())

(OUT_DIR / "main.tex").write_text(src, encoding="utf-8")
figs = OUT_DIR / "figures"
figs.mkdir(exist_ok=True)
names = sorted(re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", src))
for name in names:
    shutil.copy2(DRAFT / "figures" / f"{name}.pdf", figs / f"{name}.pdf")
with zipfile.ZipFile(OUT_DIR / "overleaf_upload.zip", "w", zipfile.ZIP_DEFLATED) as z:
    z.write(OUT_DIR / "main.tex", "main.tex")
    for name in names:
        z.write(figs / f"{name}.pdf", f"figures/{name}.pdf")
print(json.dumps({"abstract_words": words, "references": len(order), "figures": names,
                  "intro_paragraphs": intro.count("\\textbf{") + 1,
                  "related_paragraphs": related.count("\\textbf{") + 1}, indent=1))
