from __future__ import annotations

from typing import Callable


PLATE_VIDEO_MIN_CONFIDENCE = 0.80
PLATE_VIDEO_MIN_STABLE_APPEARANCES = 2
PLATE_VIDEO_SINGLE_FRAME_HIGH_CONFIDENCE = 0.95
PLATE_VIDEO_CLUSTER_SIMILARITY = 0.80


def normalize_plate_text(value) -> str:
    text = str(value or "").strip().upper()
    return "".join(
        char
        for char in text
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )


def plate_edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            insert_cost = current[right_index - 1] + 1
            delete_cost = previous[right_index] + 1
            replace_cost = previous[right_index - 1] + (
                0 if left_char == right_char else 1
            )
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current

    return previous[-1]


def plate_text_similarity(left: str, right: str) -> float:
    """比较完整车牌与去掉省份简称后的后缀相似度。"""
    left = normalize_plate_text(left)
    right = normalize_plate_text(right)

    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    full_distance = plate_edit_distance(left, right)
    full_similarity = 1.0 - full_distance / max(len(left), len(right))

    left_suffix = left[1:] if left and "\u4e00" <= left[0] <= "\u9fff" else left
    right_suffix = right[1:] if right and "\u4e00" <= right[0] <= "\u9fff" else right
    suffix_distance = plate_edit_distance(left_suffix, right_suffix)
    suffix_similarity = 1.0 - suffix_distance / max(
        1,
        len(left_suffix),
        len(right_suffix),
    )

    return round(max(full_similarity, suffix_similarity), 4)


def cluster_plate_candidates(candidates: list[dict]) -> list[dict]:
    clusters: list[dict] = []
    ordered = sorted(
        candidates,
        key=lambda item: float(item.get("confidence", 0) or 0),
        reverse=True,
    )

    for candidate in ordered:
        best_cluster = None
        best_similarity = 0.0

        for cluster in clusters:
            similarity = max(
                plate_text_similarity(
                    candidate["plate_number"],
                    member["plate_number"],
                )
                for member in cluster["members"]
            )
            if similarity > best_similarity:
                best_similarity = similarity
                best_cluster = cluster

        if (
            best_cluster is not None
            and best_similarity >= PLATE_VIDEO_CLUSTER_SIMILARITY
        ):
            best_cluster["members"].append(candidate)
        else:
            clusters.append({"members": [candidate]})

    return clusters


def build_stable_plate_from_cluster(cluster: dict) -> dict:
    members = cluster["members"]
    variant_map: dict[str, dict] = {}

    for candidate in members:
        number = candidate["plate_number"]
        variant = variant_map.setdefault(
            number,
            {
                "plate_number": number,
                "appear_count": 0,
                "max_confidence": 0.0,
                "frame_indices": [],
                "best_candidate": candidate,
            },
        )
        variant["appear_count"] += 1
        variant["frame_indices"].append(candidate["frame_index"])

        confidence = float(candidate.get("confidence", 0) or 0)
        if confidence >= float(variant["max_confidence"]):
            variant["max_confidence"] = confidence
            variant["best_candidate"] = candidate

    variants = sorted(
        variant_map.values(),
        key=lambda item: (
            float(item["max_confidence"]),
            int(item["appear_count"]),
        ),
        reverse=True,
    )

    representative_variant = variants[0]
    representative = dict(representative_variant["best_candidate"])
    unique_frames = sorted(
        {
            int(candidate["frame_index"])
            for candidate in members
            if candidate.get("frame_index") is not None
        }
    )
    confidences = [
        float(candidate.get("confidence", 0) or 0)
        for candidate in members
    ]

    representative["plate_number"] = representative_variant["plate_number"]
    representative["confidence"] = round(
        float(representative_variant["max_confidence"]),
        4,
    )
    representative["appear_count"] = len(unique_frames)
    representative["frame_indices"] = unique_frames
    representative["best_frame_index"] = representative.get("frame_index")
    representative["best_image_url"] = representative.get("image_url", "")
    representative["best_output_image_url"] = representative.get(
        "output_image_url",
        "",
    )
    representative["average_confidence"] = round(
        sum(confidences) / max(1, len(confidences)),
        4,
    )
    representative["variant_count"] = len(variants)
    representative["candidate_variants"] = [
        {
            "plate_number": item["plate_number"],
            "confidence": round(float(item["max_confidence"]), 4),
            "appear_count": int(item["appear_count"]),
            "frame_indices": sorted(set(item["frame_indices"])),
        }
        for item in variants
    ]

    representative.pop("frame_index", None)
    representative.pop("image_url", None)
    representative.pop("output_image_url", None)
    return representative


def aggregate_plate_frame_results_v2(
    frame_results: list[dict],
    compact_frame_result_fn: Callable[[str, dict], dict],
) -> dict:
    """
    低置信度过滤 + 相似字符串聚类 + 组内最高置信度选择。

    最终 plate_count 表示稳定车牌数量，而不是不同 OCR 字符串数量。
    """
    raw_candidate_count = 0
    discarded_low_confidence_count = 0
    accepted_candidates: list[dict] = []

    for item in frame_results:
        frame_index = item.get("frame_index")
        image_url = item.get("image_url", "")
        output_image_url = item.get("output_image_url", "")
        plates = item.get("result", {}).get("plates", []) or []

        for plate in plates:
            raw_candidate_count += 1
            plate_number = normalize_plate_text(
                plate.get("plate_number")
                or plate.get("plate")
                or plate.get("text")
            )
            confidence = float(plate.get("confidence", 0) or 0)

            if not plate_number or confidence < PLATE_VIDEO_MIN_CONFIDENCE:
                discarded_low_confidence_count += 1
                continue

            candidate = dict(plate)
            candidate["plate_number"] = plate_number
            candidate["confidence"] = confidence
            candidate["frame_index"] = frame_index
            candidate["image_url"] = image_url
            candidate["output_image_url"] = output_image_url
            accepted_candidates.append(candidate)

    clusters = cluster_plate_candidates(accepted_candidates)
    stable_plates: list[dict] = []
    discarded_unstable_count = 0

    for cluster in clusters:
        stable_plate = build_stable_plate_from_cluster(cluster)
        appear_count = int(stable_plate.get("appear_count", 0))
        confidence = float(stable_plate.get("confidence", 0) or 0)
        is_stable = (
            appear_count >= PLATE_VIDEO_MIN_STABLE_APPEARANCES
            or confidence >= PLATE_VIDEO_SINGLE_FRAME_HIGH_CONFIDENCE
        )

        if not is_stable:
            discarded_unstable_count += len(cluster["members"])
            continue

        stable_plates.append(stable_plate)

    stable_plates.sort(
        key=lambda plate: (
            int(plate.get("appear_count", 0)),
            float(plate.get("confidence", 0) or 0),
        ),
        reverse=True,
    )

    def best_frame_score(item: dict):
        matched_clusters: set[int] = set()
        confidence_sum = 0.0
        max_confidence = 0.0

        for plate in item.get("result", {}).get("plates", []) or []:
            confidence = float(plate.get("confidence", 0) or 0)
            if confidence < PLATE_VIDEO_MIN_CONFIDENCE:
                continue

            number = normalize_plate_text(plate.get("plate_number"))
            if not number:
                continue

            for cluster_index, stable_plate in enumerate(stable_plates):
                if (
                    plate_text_similarity(
                        number,
                        stable_plate.get("plate_number", ""),
                    )
                    >= PLATE_VIDEO_CLUSTER_SIMILARITY
                ):
                    matched_clusters.add(cluster_index)
                    confidence_sum += confidence
                    max_confidence = max(max_confidence, confidence)
                    break

        return (
            len(matched_clusters),
            round(confidence_sum, 4),
            round(max_confidence, 4),
        )

    if stable_plates:
        best = max(frame_results, key=best_frame_score)
    else:
        best = max(
            frame_results,
            key=lambda item: float(item.get("confidence", 0) or 0),
        )

    filtered_frame_results: list[dict] = []
    for item in frame_results:
        compact = compact_frame_result_fn("plate", item)
        filtered_plates = [
            plate
            for plate in (compact.get("plates") or [])
            if float(plate.get("confidence", 0) or 0)
            >= PLATE_VIDEO_MIN_CONFIDENCE
        ]
        compact["plates"] = filtered_plates
        compact["plates_count"] = len(filtered_plates)
        compact["best_plate"] = max(
            filtered_plates,
            key=lambda plate: float(plate.get("confidence", 0) or 0),
            default=None,
        )
        filtered_frame_results.append(compact)

    final_result = dict(best.get("result", {}))
    final_result["model"] = final_result.get("model", "HyperLPR3")
    final_result["plates"] = stable_plates
    final_result["plate_count"] = len(stable_plates)
    final_result["stable_plate_count"] = len(stable_plates)
    final_result["raw_candidate_count"] = raw_candidate_count
    final_result["accepted_candidate_count"] = len(accepted_candidates)
    final_result["discarded_low_confidence_count"] = (
        discarded_low_confidence_count
    )
    final_result["discarded_unstable_count"] = discarded_unstable_count
    final_result["confidence_threshold"] = PLATE_VIDEO_MIN_CONFIDENCE
    final_result["min_stable_appearances"] = (
        PLATE_VIDEO_MIN_STABLE_APPEARANCES
    )
    final_result["single_frame_high_confidence_threshold"] = (
        PLATE_VIDEO_SINGLE_FRAME_HIGH_CONFIDENCE
    )
    final_result["stream_strategy"] = (
        "连续帧抽样 + 低置信度过滤 + 相似车牌聚类 + 组内最高置信度"
    )
    final_result["aggregation_version"] = (
        "plate_video_v2_confidence_cluster"
    )
    final_result["best_frame_index"] = best.get("frame_index")
    final_result["best_frame_plate_count"] = best_frame_score(best)[0]
    final_result["sampled_frames"] = len(frame_results)
    final_result["frame_results"] = filtered_frame_results

    return {"best": best, "final_result": final_result}
