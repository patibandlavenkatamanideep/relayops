"""Classification metrics — accuracy, per-class P/R/F1, and a confusion matrix.

Pure stdlib (no scikit-learn) so it runs offline; the same harness is reused for
the LLM-judge eval in step 5.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClassMetrics:
    label: str
    precision: float
    recall: float
    f1: float
    support: int


def accuracy(y_true: list[str], y_pred: list[str]) -> float:
    if not y_true:
        return 0.0
    correct = sum(1 for t, p in zip(y_true, y_pred) if t == p)
    return correct / len(y_true)


def confusion_matrix(
    y_true: list[str], y_pred: list[str], labels: list[str]
) -> dict[str, dict[str, int]]:
    """cm[true][pred] = count."""
    cm = {t: {p: 0 for p in labels} for t in labels}
    for t, p in zip(y_true, y_pred):
        cm[t][p] += 1
    return cm


def per_class_metrics(
    y_true: list[str], y_pred: list[str], labels: list[str]
) -> list[ClassMetrics]:
    out: list[ClassMetrics] = []
    for label in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != label and p == label)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == label and p != label)
        support = sum(1 for t in y_true if t == label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        out.append(ClassMetrics(label, precision, recall, f1, support))
    return out


def macro_f1(y_true: list[str], y_pred: list[str], labels: list[str]) -> float:
    cm = per_class_metrics(y_true, y_pred, labels)
    return sum(m.f1 for m in cm) / len(cm) if cm else 0.0


def format_confusion_matrix(cm: dict[str, dict[str, int]], labels: list[str]) -> str:
    w = max((len(l) for l in labels), default=4)
    cw = max(w, 4)
    header = " " * (w + 2) + " ".join(l[:cw].rjust(cw) for l in labels)
    lines = [header, " " * (w + 2) + "(predicted ->)"]
    for t in labels:
        row = " ".join(str(cm[t][p]).rjust(cw) for p in labels)
        lines.append(f"{t.rjust(w)}  {row}")
    return "\n".join(lines)


def classification_report(
    y_true: list[str], y_pred: list[str], labels: list[str]
) -> str:
    rows = per_class_metrics(y_true, y_pred, labels)
    w = max((len(l) for l in labels), default=8)
    head = f"{'label'.rjust(w)}  prec   rec    f1     support"
    lines = [head]
    for m in rows:
        lines.append(
            f"{m.label.rjust(w)}  {m.precision:.2f}  {m.recall:.2f}  {m.f1:.2f}   {m.support}"
        )
    lines.append("")
    lines.append(f"accuracy: {accuracy(y_true, y_pred):.3f}   macro-F1: {macro_f1(y_true, y_pred, labels):.3f}")
    return "\n".join(lines)
