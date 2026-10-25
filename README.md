# Invisible Grocery Assistant

摄像头驱动的家庭自动补货原型。用户通过语音交流，没有图形化界面。

已实现：DeepSeek 图片输入适配器、真实的工具调用循环、SQLite 库存与订单、Markdown 长期偏好、历史消耗摘要、购物车、macOS 语音输入与播报，以及明确标注的本地模拟采购。

**当前 Whole Foods 采购仅为本地模拟，没有真实结账、支付或配送集成。离线 replay 模式不调用任何 LLM，也不识别图片。**

## 快速验证（无需密钥，无第三方 Python 依赖）

在本目录执行：

```sh
python3 -m grocery doctor
python3 -m grocery demo --reset
python3 -m unittest discover -s tests -v
```

离线 demo 回放合成感知结果与两句合成用户回答，自动生成鸡蛋、无糖燕麦奶、两盒酸奶的模拟订单。它验证库存与采购流程，不能证明真实视觉或语音识别效果。`--reset` 只清除本项目 runtime 目录里的 demo 数据库和 demo 偏好副本。

增加 `--inbound-milk` 可验证牛奶已有配送时不会重复购买。增加 `--presence away` 可验证无人时按演示授权自动提交模拟订单。全程不会真的采购。

## 配置 DeepSeek

`.env` 已创建，内容为空白配置。使用文本编辑器在本地填入：

```dotenv
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
VOICE_LOCALE=en-US
MACOS_VOICE=
```

无需安装 OpenAI SDK，代码通过 HTTPS 调用 DeepSeek。若 Python 未安装 CA 证书，在线调用会使用 `certifi` 的证书包；可用 `python3 -m pip install certifi` 安装。证书和域名验证始终开启。密钥和 runtime 已加入 `.gitignore`，不要把密钥粘贴到聊天。`doctor` 只输出是否已配置，不输出密钥。

DeepSeek 官方当前的 `deepseek-flash` 支持图片、JSON 输出和工具调用；模型名可通过环境变量修改。主程序显式关闭 thinking 以缩短演示等待。账户权限、网络及实际识别质量仍需在线验证。

## 全语音演示

先编译系统语音入口：

```sh
python3 -m grocery voice-build
```

然后运行：

```sh
python3 -m grocery demo --reset --mode deepseek --voice mic
```

此命令真实调用 DeepSeek 规划与理解加购，使用系统麦克风、语音识别和音箱；冰箱输入仍是明确标注的合成感知回放。

系统会播报鸡蛋和牛奶的补货清单。可以说“再加两盒无糖酸奶”，再说“就这些”，系统自动提交模拟订单。可以说“取消”停止此次采购。说“以后牛奶都买普通牛奶”会更新演示偏好文件；一次加购只影响购物车。

首次使用时 macOS 会请求麦克风与语音识别权限。识别优先使用设备端能力，不可用时可能使用 Apple 系统在线识别。录音仅发生在助手播报结束后的加购轮次，不会一直录音；本版本不保存原始音频。代码未在此次开发过程中实际打开麦克风。当前 Codex 运行环境的权限检查显示麦克风与语音识别被拒绝；请在实际启动程序的终端环境运行，并在系统设置的“隐私与安全性”中为对应程序开启这两项权限。

若要先验证音频链路而不配置模型，可以使用 `--mode replay --voice mic`。此时只有语音识别和播报真实运行，语义解析是有限规则，仅用于明确标注的排练。

## 加入真实冰箱照片

把照片放在项目内，例如 `data/my_fridge.jpg`，再执行：

```sh
python3 -m grocery demo --reset --mode deepseek --voice mic --image data/my_fridge.jpg
```

此时后续冰箱画面真实交给 DeepSeek 识别，初始画面与历史仍是合成演示数据。现在提供 31 类食品的识别、语音加购和库存规则；具体计数单位来自 `grocery/catalog.py`，例如鸡蛋按枚、牛奶按盒、蔬菜按个或包装。不透明包装的剩余体积不在本版能力范围内。模型若不能明确计数，返回不确定，不触发相关商品采购。只有库存确实低于历史推导的目标，才会建立购物车。

For an audible photo inspection, add `--describe`, or run `run-photo-test.command`. This reads the model's estimated quantities and uncertainty aloud. If the current image and demo history do not indicate a shortage, the test reports that no order is needed and finishes without asking for additions. Normal operation keeps its quiet behavior.

## 后台模拟摄像头持续输入

```sh
python3 -m grocery watch --mode deepseek --voice mic --frames-dir data/live_frames
```

程序监测目录中新写入的图片，自动识别并触发后续流程，不要求家庭用户上传或点击。适合由另一位演示者在后台放入照片，或后续接入摄像头。`watch` 使用独立的真实家庭数据库，未导入真实历史前不会编造消耗需求。用于展示时，推荐前面的 `demo --image` 命令。

在家传感器当前由 `--presence home/away` 模拟。默认人在家但未听清回答时保留草稿；可通过事先配置的 `--silence-policy continue` 模拟无人回应时按原授权继续。新增商品在结束加购后统一提交，不额外要求反复批准常规补货。默认预算无上限；金额只用于播报和记录，不拦截下单。

## 长期记忆与数据位置

- `data/household_preferences.md`：真实家庭模板，目前没有任何个人偏好。
- `data/demo_preferences.md`：标为合成数据的演示偏好种子。
- `runtime/demo_preferences.md`：演示期间实际读取、更新的 Markdown 文件。
- `runtime/demo.sqlite`：合成演示库存、历史、购物车和模拟订单。
- `runtime/household.sqlite`：`run/watch/status` 使用的独立家庭数据库。
- `runtime/events.jsonl`：工具调用记录，供开发者验证，非用户界面。

明确长期偏好按类别替换旧条目，临时加购不写入。历史统计只更新 Markdown 的推测摘要区，不覆盖用户明确要求。摄像头画面减少属于消耗估计，不能证明食品实际被吃掉；统计和菜品偏好推断仍待真实数据验证。

## 文件分工

- A：`grocery/providers.py` 的视觉入口、`grocery/core.py` 的库存/历史/补货。
- B：`grocery/agent.py` 的工具循环、`grocery/voice.py`、`native/VoiceListener.swift`、`grocery/__main__.py` 的事件流程。
- 采购适配器当前为 `Grocery.checkout()` 的本地事务。后续真实 Whole Foods 集成需验证账户接入、购物车、结账、订单成功结果，不能用模拟返回替代。

所有状态由一个后台主进程串行处理。当前没有多进程偏好写入锁、真实家庭账户隔离、完整菜品学习或实际硬件协议。


## English voice interaction

Set `VOICE_LOCALE=en-US` in `.env`. The microphone locale, spoken prompts, product names, scripted demo replies, and agent response language follow this setting. With `MACOS_VOICE` left blank, English playback selects an available U.S. English system voice.

Example replies: “Add two unsweetened yogurts,” “That's all,” “Cancel this order,” or “From now on, always buy regular milk.” One-time additions stay in the current cart; explicit long-term preferences update the Markdown memory.

## 官方参考

- [DeepSeek Vision](https://api-docs.deepseek.com/guides/vision/)
- [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)
- [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
- [Apple Speech audio buffer recognition](https://developer.apple.com/documentation/speech/sfspeechaudiobufferrecognitionrequest)

## Voice additions and unavailable products

The shared demo catalog supports 31 food categories and 32 product choices, covering dairy, produce, proteins, bread, drinks, and condiments. Apples, onions, tomatoes, and avocados are sold by each; garlic is sold by bulb. These are synthetic demo products and prices, not live Whole Foods listings. The same category list and counting units are supplied to the image model. Actual recognition quality depends on the image; visibly obscured categories remain uncertain. Supported foods not seen in a photo are assumed empty by default; use --unseen-policy unknown for conservative handling.

“Please add two apples” now adds two individual apples. If a product is outside the catalog, the assistant explains that it could not add the item and holds checkout. Say “Skip it, that's all” to continue without that item, or cancel. Technical tool failures are logged with the failed tool name; they do not count as successful cart updates.

To reproduce the apples request with a live model and scripted audio input, without touching the household state or placing a real order:

```sh
python3 scripts/test_live.py --request "Please add two apples" --expect-sku apples_each --expect-quantity 2
```

## Full-fridge scenario and expanded foods

`grocery/catalog.py` is the shared source for product names, observation units, voice aliases, synthetic prices, full-stock quantities, and synthetic consumption rates. It now covers 31 categories: eggs, milk, yogurt, cheese, butter; apples, onions, tomatoes, garlic, avocados, carrots, broccoli, spinach, lettuce, cucumbers, bell peppers, mushrooms, lemons, oranges, strawberries, blueberries; chicken, fish, tofu; bread, juice, bottled water, sparkling water, jam, mustard, and ketchup.

Inspect `data/full_fridge_inventory.md` for the quantities. To hear the fully stocked scene, run `run-full-fridge-test.command`, or:

```sh
python3 -m grocery demo --reset --mode replay --scenario full --describe --speak
```

The full scene should produce no restock order. The default restock scene keeps all 31 categories but reduces eggs and milk, preserving the existing purchase demonstration. The broad food list describes prototype capabilities, not the household's confirmed preferences.

For real photos, synthetic consumption history is still explicitly labelled as demo data, but no synthetic full-fridge counts are preloaded into the current inventory. The default photo policy treats supported categories not seen or omitted by the vision model as assumed empty (zero). Visible but ambiguous items remain uncertain. Assumptions retain zero visual confidence, are labelled in inventory, and do not create consumption-history events. Use --unseen-policy unknown to leave unseen categories unknown. Visible-area fullness is a qualitative estimate (`nearly_empty`, `low`, `medium`, `high`, `full`, `unknown`), not measured liters or exact physical capacity.

Application prompts, tool descriptions, command help, logs, errors, and factual decision summaries are in English. Internal model thinking is disabled; the visible decision summary reports inventory and restock targets.

## Complete voice purchase workflow

The assistant reads the initial restock proposal and estimated total, then asks for additions or adjustments. Say, for example, "Remove milk and add two apples," "Set apples to three," and then "That's all." In live mode, DeepSeek interprets these requests through `edit_cart`; removal uses `operation=set` and `packs=0`. The offline replay supports these example commands too.

After the conversation finishes, the assistant reads the complete final list and total aloud. It saves an English Markdown document at `runtime/purchase_lists/<cart-id>-v<version>.md`, with a convenient latest copy at `runtime/purchase_list.md`. The document contains the initial proposal, voice updates, final SKUs and sales-unit quantities, synthetic prices, total, and checkout decision. These are factual workflow records, not private model reasoning.

Only after the list is saved and the final cart passes validation does the controller emit `runtime/order_signals/order-<cart-id>-v<version>.json`. The `grocery.order_requested` event includes the cart ID/version, final lines, total, Markdown path and checksum, and checkout idempotency key. It is explicitly marked `simulated: true` with `adapter: local_simulation`. The local checkout simulator uses that signal's cart version and idempotency key and validates again. An order-request event is not proof of actual delivery or a live Whole Foods purchase.

The budget is unlimited: there is no spending-limit check. Prices and totals are still calculated and recorded. When the order signal has been successfully saved, the assistant says exactly `ordered`. This is the final spoken line; the checkout adapter remains a local simulation. If the completed cart is empty or its inventory/version validation fails, the list is saved with an on-hold explanation and no order signal is emitted. Cancellation, unresolved requests, or an unfinished conversation do not emit a signal. Away mode and the preset silence policy retain their configured automatic behavior.
