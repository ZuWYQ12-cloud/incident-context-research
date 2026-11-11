"""Validate source declarations without inferring telemetry completeness."""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_sources(data):
    sources = data.get("sources")
    observations = data.get("observations")

    require(isinstance(sources, list) and sources, "sources must be nonempty")
    require(isinstance(observations, list), "observations must be a list")

    registry = {}
    for source in sources:
        require(isinstance(source, dict), "Source must be an object")
        identifier = source.get("id")
        require(
            isinstance(identifier, str) and identifier.strip(),
            "Source ID must be nonempty",
        )
        require(identifier not in registry, f"Duplicate source: {identifier}")

        for field in ("kind", "component", "coverage_basis", "note"):
            value = source.get(field)
            require(
                isinstance(value, str) and value.strip(),
                f"{identifier}: missing or empty {field}",
            )

        availability = source.get("availability")
        representation = source.get("representation")
        completeness = source.get("completeness")

        require(
            availability in ("provided", "not_provided"),
            f"{identifier}: invalid availability",
        )
        require(
            completeness in ("partial", "unknown"),
            f"{identifier}: unsupported completeness claim",
        )
        require(
            "coverage_interval" in source
            and source["coverage_interval"] is None,
            f"{identifier}: confirmed coverage intervals are not supported yet",
        )

        if availability == "provided":
            require(
                representation in ("discrete_checks", "selected_excerpts"),
                f"{identifier}: invalid representation",
            )
        else:
            require(
                representation == "none" and completeness == "unknown",
                f"{identifier}: absent source must have unknown completeness",
            )

        registry[identifier] = {
            "availability": availability,
            "completeness": completeness,
            "observation_ids": [],
        }

    seen = set()
    for observation in observations:
        require(isinstance(observation, dict), "Observation must be an object")
        identifier = observation.get("id")
        require(
            isinstance(identifier, str) and identifier.strip(),
            "Observation ID must be nonempty",
        )
        require(identifier not in seen, f"Duplicate observation: {identifier}")
        seen.add(identifier)

        source_id = observation.get("source_id")
        require(
            isinstance(source_id, str) and source_id in registry,
            f"{identifier}: unknown source {source_id}",
        )
        entry = registry[source_id]
        require(
            entry["availability"] == "provided",
            f"{identifier}: observation belongs to a source marked not_provided",
        )
        entry["observation_ids"].append(identifier)

    for identifier, entry in registry.items():
        if entry["availability"] == "provided":
            require(
                bool(entry["observation_ids"]),
                f"{identifier}: provided source has no observations",
            )

    return registry
