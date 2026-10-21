"""genfstab includes active live-environment swaps even outside the target root."""


def without_live_swap(generated: str) -> str:
    result = []
    for line in generated.splitlines():
        fields = line.split()
        if (
            fields
            and not line.lstrip().startswith("#")
            and len(fields) >= 3
            and fields[2] == "swap"
        ):
            continue
        result.append(line)
    return "\n".join(result).rstrip() + "\n"
