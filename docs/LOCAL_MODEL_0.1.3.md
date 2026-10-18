# 本地模型与 API 服务：0.1.3

## 已验证的本机配置

- NVIDIA GeForce RTX 5060 Laptop GPU，8GB显存；约32GB系统内存，Core Ultra 9 275HX。
- 沿用历史成功模型 Qwen3-4B-Q4_K_M.gguf，文件约2.50GB。
- 本机安装的 llama-cpp-python 0.3.36 为CPU构建。其server默认 logits_all=true，在32K上下文下分配32768×151936×4字节，即18.55GiB的scores数组；这还不是总内存需求。
- 已部署官方 llama.cpp b11669 Windows Vulkan 后端，指定NVIDIA设备Vulkan0，16K上下文，单并发，512/256批处理，KV缓存q8_0，Flash Attention开启，模板关闭思考，监听仅127.0.0.1。
- Windows Vulkan发布包SHA256：2c07574e7bde71321cb785282b20cc821e951132149eeceeea32ddebdbf0598e。来自官方ggml-org/llama.cpp release b11669。
- CPU备用明确使用 logits_all=false、n_batch=256、n_gpu_layers=0，最多8192上下文。它不宣称使用GPU。

## 软件接入

`desktop/local_llm.py` 负责有限范围的后端发现、命令构造和安全错误分类。可用 `MEETINGVAR_LLAMA_SERVER` 或本机配置 `llm_server_exe` 指定原生程序。原生运行库不打入仓库，也不随UI壳安装包分发。

新增配置：`llm_backend`（auto/native/python）、`llm_server_exe`、`llm_context_tokens`、`llm_gpu_layers`、`llm_device`、`llm_reading_max_tokens`、`llm_api_context_tokens`、`llm_thinking`。当前本机选择native/Vulkan0/16384，读取输出预算4096。其他机器应按实际设备设置，不照搬Vulkan设备编号。

点击启动服务会检查进程存活、服务响应和实际JSON生成，不只返回Popen成功；重复点击不会创建第二个加载进程。返回结构保留旧字段，新增backend/context_tokens/state/error。`model`保留服务实际模型ID，`display_model`可供UI使用，不能把截短后的文件名送回API。

项目读取与生成测试共用 `_llm_env()` 和 `chat_result()`；API兴趣判断同样复用环境解析，但保留独立1600输出预算。填写配置与生成已验证分开；验证指纹含有效参数和凭据版本，仅保存摘要，参数变化失效。成功验证缓存最长24小时，并不承诺后续网络一直可用。

读取分块以UTF-8字节保守上界估算，明确扣除完整提示、输出预留、消息开销和安全余量；不冒充精确tokenizer计数。计划与执行使用相同有效参数，默认单块不超过6000字符。缓存v2按端点、完整模型名、提示版本和预算隔离，旧缓存保留但不误用。失败、取消和试读不会覆盖之前成功的项目上下文。

API错误明确区分鉴权、模型不存在、额度、限流、网络、空正文、输出截断、JSON格式和预算，不打印Key或响应原文。`deepseek-flash`已通过实际合成测试，无需仅因旧提示而改模型名。

## 验证边界

- GPU单独启动约5.75秒；软件启动加JSON验证约5.70秒。
- 软件完整读取两份合成文档：本地两块完成约3.19秒，DeepSeek API两块完成约11.33秒，均完整发布项目上下文。
- 较长合成提示的GPU试跑：输入1756 tokens，返回有效JSON约9.33秒；该次短输出计时约90 token/s，仅描述该样本，不是长期吞吐承诺。
- 测试期间整卡显存使用约5518MiB，剩余约2382MiB；系统其他进程和负载会改变这些数值。
- 真实DeepSeek API兴趣判断合成测试：2.75秒返回1个DECISION，引用ID全部对应输入原句，无错误；仅证明这条合成样例与接口闭环，不代表真实会议判准率。
- 134项针对性模拟回归通过。最小依赖检查发现380项，执行339项通过、按依赖跳过41项，编译检查通过。
- 合成测试使用真实本地GPU和用户已配置云端API，但没有发送用户项目或会议内容；不能据此认定任意真实材料的理解质量达标。

本轮保持 `desktop/web/**` 不变，便于UI队友继续并行。UI可继续使用既有调用，后续可按新增状态字段改善展示。
