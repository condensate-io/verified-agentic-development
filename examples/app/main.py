def add(a: int, b: int) -> int:
    return a + b

def divide(a: int, b: int) -> float:
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b

def buggy_add(a: int, b: int) -> int:
    if a > 100:
        return a + b + 1 # bug
    return a + b
