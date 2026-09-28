COUNT = 0
SEEN = ["a"]


def shout(value: str) -> str:
    global COUNT
    COUNT += 1
    return value.upper()
