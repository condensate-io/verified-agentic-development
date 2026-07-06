def summarize_status(tasks):
    summary = {"active": 0, "blocked": 0, "passed": 0, "unknown": 0}
    for task in tasks:
        status = task.get("status", "unknown")
        if status not in summary:
            status = "unknown"
        summary[status] += 1
    return summary
