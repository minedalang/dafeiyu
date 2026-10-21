# 可选的原创蓝鲸 WebM 试验素材（2026-10-21）

> **注意：这不是原版 `shenshen` 高清角色的同款动画。** 本次上传的 Windows-Conda 源码包未包含 106 段角色视频素材，因此这三段由 Python 绘制，是独立的 Q 版蓝鲸小样，用于预览动作创意和验证透明 VP9 播放。未经用户选择，不覆盖任何原动作。

| 动画文件名 | 对应 AI 语义活动 | 特点 |
| --- | --- | --- |
| `补充体力-喝热茶.webm` | `rest` | 托起热茶杯、热气浮动；适用于精力低但未困的时段 |
| `恢复元气-轻轻伸展.webm` | `rest` | 抬起两侧鳍、伸展、星光；可循环播放 |
| `番茄钟-专注读书.webm` | `work` | 读书并带番茄计时器；番茄钟不会自动强制切动画 |

全部为 640×360 / 12 FPS / 每圈 4 秒 / WebM VP9 alpha (`alpha_mode=1`) / 无音轨；动画按时间正弦周期制作，首尾相接。

## 安装可选素材

1. 解压补丁中的 `experimental_webm/` 目录，先打开 `PREVIEW.jpg` 查看画风是否符合预期。
2. 如果你**接受与原版不完全相同的 Q 版鲸鱼画风**，关闭桌宠后，把选中的 `.webm` 复制到：`<项目根目录>/assets/characters/shenshen/videos/random/` （若原角色素材放在外部 `characters/shenshen/videos/`，则放该实际被使用的目录的 `random/`）。
3. 重启桌宠；程序使用文件名扫描素材。对应活动在 `pet/autonomy_intent.py` 中已注册，只有实际存在的 WebM 才会发给 AI；未复制这些文件时仍然只会使用原来的动作。
4. **不要替换** `idle/` 或原有同名 WebM。不要仅为安装这三个试验素材把一个已在使用的外部角色目录覆盖掉。

`docs/MECHANISM-REFERENCE.md` 中的 AI 活动推荐并不强制播放新素材。若希望得到**原版角色一致**的高质量新动作，请另提供任一原始透明 WebM 或整套角色素材目录，方便直接基于原动画角色作动作变化。

## 重新生成（开发者）

使用 `pip install Pillow`，并确保 `ffmpeg` 在 PATH 中；执行 `python scripts/make_fat_whale_webm.py --output experimental_webm`。生成脚本完全本地运行、不联网，也不读取用户截图、聊天或 API Key。
