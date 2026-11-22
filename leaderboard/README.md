# Leaderboard 目录与信任域隔离规范

- `blind/<pack_id>/`：唯一可交付给独立评委的脱敏目录（内含《统帅调度档案》、脱敏代码与《官方 5 级平庸统帅参照样本》）。
- `blind-maps/`：模型身份与 `pack_id/case_id` 的私有映射，**严禁对评委和选手暴露**。
- `reviews.jsonl`：中心化追加式盲评日志，评委仅通过 `src/scoring/judge_submit.py` 写入，**严禁评委读取他人打分**。
- `objective-cache/`：经双指纹防篡改签名的客观分（A/B/C）缓存。
- `verdicts/`：由 `leaderboard/finalize.py` 合成的各模型终局裁决留档。
- `scores.md`：由 `leaderboard/finalize.py` 唯一写入的官方总排行榜。
