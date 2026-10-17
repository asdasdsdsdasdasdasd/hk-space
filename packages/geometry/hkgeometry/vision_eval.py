"""视觉结构审计的计分。预测和标准答案都是房间/门/邻接集合。"""
from __future__ import annotations


def _set(rows, key):
    return {row.get(key) for row in rows if row.get(key)}


def score_structure(predicted: dict, truth: dict) -> dict:
    pred_rooms = {r["id"]: r for r in predicted.get("rooms") or []}
    true_rooms = {r["id"]: r for r in truth.get("rooms") or []}
    ids = set(true_rooms)
    room_hit = sum(1 for i in ids if pred_rooms.get(i, {}).get("label") == true_rooms[i].get("label"))
    door_hit = 0
    door_n = 0
    adj_hit = 0
    adj_n = 0
    for i, truth_room in true_rooms.items():
        got = pred_rooms.get(i) or {}
        for door in truth_room.get("doors") or []:
            door_n += 1
            match = next((d for d in got.get("doors") or [] if d.get("id") == door.get("id")), None)
            if match and match.get("wall_side") == door.get("wall_side"):
                door_hit += 1
        truth_adj = set(truth_room.get("adjacent_to") or [])
        pred_adj = set(got.get("adjacent_to") or [])
        adj_n += len(truth_adj)
        adj_hit += len(truth_adj & pred_adj)
    truth_severe = set(truth.get("severe") or [])
    pred_severe = set(predicted.get("severe") or [])
    recall = (len(truth_severe & pred_severe) / len(truth_severe)) if truth_severe else 1.0
    false_corr = len(pred_severe - truth_severe)
    return {
        "room_label_accuracy": room_hit / len(ids) if ids else 1.0,
        "door_wall_accuracy": door_hit / door_n if door_n else 1.0,
        "adjacency_accuracy": adj_hit / adj_n if adj_n else 1.0,
        "severe_error_recall": recall,
        "false_corrections": false_corr,
    }
