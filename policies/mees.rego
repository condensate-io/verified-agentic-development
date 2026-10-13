package vad.minimal_effort

default allow = false
default warn = false

# Pass: MEES >= 70 and within budget
allow {
    input.mees >= 70
    input.changed_files <= input.budget.max_changed_files
    input.complexity_delta <= input.budget.max_complexity_increase
}

# Warn: MEES 50-69 or slightly over budget
warn {
    input.mees >= 50
    input.mees < 70
}

warn {
    input.changed_files > input.budget.max_changed_files
    input.changed_files <= input.budget.max_changed_files + 3
}

# Deny reasons (for MR comment)
deny[msg] {
    input.mees < 50
    msg := sprintf("MEES score %d is below minimum threshold (50)", [input.mees])
}

deny[msg] {
    input.changed_files > input.budget.max_changed_files + 3
    msg := sprintf("Changed %d files exceeds budget (%d) + tolerance (3)", [input.changed_files, input.budget.max_changed_files])
}

deny[msg] {
    input.complexity_delta > input.budget.max_complexity_increase + 2
    msg := sprintf("Complexity increase %d exceeds budget (%d) + tolerance (2)", [input.complexity_delta, input.budget.max_complexity_increase])
}

deny[msg] {
    input.new_dependencies > 0
    not input.budget.allow_new_dependencies
    msg := sprintf("Added %d new dependencies but budget disallows new deps", [input.new_dependencies])
}
