# Aster 聊天翻译

聊天翻译工具。解压后双击 `Aster聊天翻译.exe` 即可运行，无需安装。

> 当前只适配《魔兽世界》的聊天格式，其它游戏暂不支持。

## 首次使用

1. 双击 exe，右下角托盘出现星形图标
2. 右键托盘 → 设置
3. 「翻译区域」→ 重新框选，框住聊天窗口
4. 「翻译模式」二选一：
   - 离线词库 —— 不需要任何 Key，翻译质量较差
   - AI 模型 —— 需要填 API Key（支持 DeepSeek 等 OpenAI 兼容接口）
5. 按下快捷键实时翻译；另一个快捷键看上次翻译（默认小键盘 `+` / `−`，可在设置里改）

## 注意

- 只适配《魔兽世界》的聊天格式，其它游戏暂不支持
- AI 模式下截图会发送到你配置的接口（含其他玩家名字与聊天内容），介意的话用离线模式
- API Key 明文保存在 config.json，请勿把整个文件夹发给别人
- 需要 WebView2 运行时（Win10/11 一般已自带）

## 许可

- 代码：[MIT](LICENSE)
- 第三方组件与词表：[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)

## 问题反馈

[Issues](https://github.com/AsterSpark/aster-chat-translator/issues)

## 如果想支持我或者关注更多动态

[爱发电](https://afdian.com/a/AsterSpark)
