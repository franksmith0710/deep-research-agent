
╔═══════════════════════════════════════════════════════════════════════════════════════════════════╗
║                     Deep Research Agent — 完整流程图（基于实际代码）                               ║
╚═══════════════════════════════════════════════════════════════════════════════════════════════════╝

  全图 12 个 functional 节点 + 1 个 HITL 中断节点 = 13 个注册节点
  3 条意图路径 ── 1 个 HITL 中断点 ── 1 个 assess 搜索回环（仅 deep_research）

                          入口
                       query + session_id
                            │
            ┌────────────────────────────────────────────┐
            │ resolve_context_node  [async]  (L113-121)   │
            │   输入: query, session_id                   │
            │   从 research_tasks 读 L0 摘要 × 10         │
            │   resolve_query() → 指代消解 + 口语改写      │
            │   BGE 余弦 < 0.2 → 回退原话                  │
            │   输出: resolved_query                      │
            └───────────────────┬────────────────────────┘
                                │
            ┌────────────────────────────────────────────┐
            │ intent_classifier_node  [async]  (L126-143) │
            │   输入: resolved_query, sections, 已搜轮次    │
            │   LLM chat_json → 3 意图:                    │
            │     deep_research / refine_section /        │
            │     simple_llm                              │
            │   输出: intent, refine_section_name          │
            └─────┬───────────┬────────────────┬──────────┘
                  │           │                │
           ┌──────┴────┐ ┌───┴─────┐  ┌───────┴──────────┐
           │ simple_   │ │ deep_   │  │ refine_section   │
           │ llm       │ │ research│  │                  │
           └──────┬────┘ └────┬────┘  └────────┬─────────┘
                  │           │                 │
           ┌──────┴──────┐   │           ┌──────┴──────────┐
           │ simple_llm  │   │           │ planner_node    │
           │ [async]     │   │           │ [async]         │
           │ (L774-792)  │   │           │ (L170-190)      │
           │ 读 L0 摘要  │   │           │ 检索 L1 + sub_q │
           │ 当历史上下文 │   │           └──────┬─────────┘
           └──────┬──────┘   │                    │
                  │      ┌───┴────────────┐       │
           ┌──────┴──┐   │ clarify_node   │      │
           │ memory  │   │ [async]        │      │
           │ [async] │   │ (L148-165)     │      │
           │ clone   │   │ scope已有?     │      │
           │ L0 only │   │ →跳过 LLM      │      │
           └────┬─────┘   └───┬──────┬────┘      │
                │             │      │           │
               END       ┌────┴──┐   │           │
                         │need_  │   │           │
                         │scope? │   │           │
                         ├─true  │   │           │
                         │→hitl_ │   │           │
                         │ scope │   │           │
                         ├─false │   │           │
                         │→plann-│   │           │
                         │ er    │   │           │
                         └────┬──┘   │           │
                              │      │           │
                       ┌──────┴──┐   │           │
                       │hitl_    │   │           │
                       │scope    │   │           │
                       └───┬─────┘   │           │
                           │         │           │
                           └────┬────┘           │
                                │                │
                                └───────┬────────┘
                                        │
                                ┌───────┴───────────┐
                                │ search  [sync]     │
                                │ (L209-283)         │
                                │ _searched_queries  │
                                │ 过滤已搜过的 query  │
                                │ Metaso→DDG 串行    │
                                │ 5 并行，×3 条      │
                                │ deep_search_count+1│
                                └───────┬───────────┘
                                        │
                                ┌───────┴───────────┐
                                │ scrape  [sync]     │
                                │ (L301-354)         │
                                │ _scrape_cache(600s)│
                                │ 多线程 5 页并发     │
                                │ 失败→snippet 降级  │
                                │ 记录来源可信度     │
                                └───────┬───────────┘
                                        │
                                ┌───────┴───────────┐
                                │ context_mgr  [async]│
                                │ (L359-410)         │
                                │ 跳过过短页          │
                                │ rerank 阈值 0.5    │
                                │ batch_brief_from   │
                                │ _pages()           │
                                └───────┬───────────┘
                                        │
                                ┌───────┴───────────┐
                                │ dedup_rerank [sync]│
                                │ (L415-464)         │
                                │ BGE 去重 (0.85)    │
                                │ CrossEncoder 打分  │
                                │ top 15 排序        │
                                └───────┬───────┬───┘
                                        │       │
                                ┌───────┴──┐ ┌──┴──────────┐
                                │ assess   │ │ report      │
                                │ [async]  │ │ refine 跳过 │
                                │ (L469-622)│ │ assess     │
                                │ 见下方    │ └──────┬─────┘
                                └───┬──────┘        │
                                    │                │
                              ┌──────┘                │
                      ┌───────┴───────────────────────────────┴──────┐
                      │ route_assess (L868-887)                      │
                      ├─ _llm_fallback → report                      │
                      ├─ sufficient → report                         │
                      ├─ intent != deep_research → report            │
                      ├─ coverage < 50 && round < 3 → search        │
                      └─ else → report                               │
                     └────┬──────────────────────────┬───────┘
                          │                          │
                   ┌──────┴──────┐         ┌─────────┴──────────┐
                   │ search      │         │ report             │
                   │ (回环)       │         │ [async]            │
                   └─────────────┘         │ (L627-695)          │
                                           │ 见下方详细         │
                                           └─────────┬──────────┘
                                                     │
                                           ┌─────────┴──────────┐
                                           │ memory  [async]     │
                                           │ (L737-762)          │
                                           │ L0 压缩摘要+写表    │
                                           │ chat_history        │
                                           │ L1(仅deep/refine)   │
                                           └─────────┬──────────┘
                                                     │
                                                    END

  ┌─ HITL 中断节点 ──────────────────────────────────────────────────────┐
  │                                                                      │
  │ hitl_scope_node  [sync]  (L797-822)                                  │
  │   interrupt → 前端显示维度选择器 + 自由输入框                         │
  │   用户选择维度 + 补充文本 → resume_data                               │
  │   按选中的维度关键词过滤 sub_queries                                   │
  │   输出: need_scope=False, sub_queries(过滤后), user_supplement        │
  │   路由: → planner（重新规划，携带 supplement）                          │
  │                                                                      │
  └──────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════
              路由条件明细（graph.py + nodes.py）
═══════════════════════════════════════════════════════════════════════════

  route_by_intent(intent_classifier →)  (L849-856):
    ├─ intent == deep_research → clarify
    ├─ intent == refine_section → planner
    └─ else → simple_llm

  clarify → lambda edge (graph.py L74):
    ├─ state.need_scope == True → hitl_scope
    └─ else → planner

  planner →（无条件 edge graph.py L78）:
    └─ → search（clarify 已在 planner 前处理）

  route_after_rerank(dedup_rerank →)  (L859-865):
    ├─ intent == refine_section → report（跳过 assess）
    └─ else → assess

  route_assess(assess →)  (L868-887):
    ├─ _llm_fallback == True → report
    ├─ sufficient == True → report
    ├─ intent != deep_research → report（安全兜底）
    ├─ coverage < 50 && assess_round < 3 → search（回环）
    └─ else → report

═══════════════════════════════════════════════════════════════════════════
                    特殊路径标注
═══════════════════════════════════════════════════════════════════════════

  ◆ query 改写熔断路径：
    resolve_context_node → resolve_query(query, session_history)
    LLM 消解指代 + 口语→专业改写
    BGE 余弦相似度对比 query vs resolved_query
    → 相似度 < 0.2 → 回退用户原话，防止 LLM 改写跑偏全链路

  ◆ _llm_fallback 降级路径（搜索全失败时）：
    search_node 标记 search_all_failed=True
    assess_node 检测 search_all_failed && findings 为空
    → 立即标记 _llm_fallback=True
    route_assess 检测 _llm_fallback → 直接到 report
    report_node 调用 LLM 直答（用自身知识）

  ◆ search 回环去重机制：
    _searched_queries 列表记录每轮已搜的子查询
    回环进入 search_node → 过滤掉已搜的子查询
    → 防止 assess 回环重复搜索相同 query
    → 全部搜过则直接返回空结果（search_all_failed 不变）

  ◆ scrape 缓存机制：
    _scrape_cache: dict[str, tuple[timestamp, result]]
    TTL = 600s（10 分钟）
    assess 回环再次进入 scrape_node → 走缓存，不重复抓取

  ◆ assess 硬指标（代码计算，非 LLM）：
    4 维度满分 80：
      ① 用户问题匹配度 0-30      → LLM 从 [30,20,10,5] 选
      ② 覆盖范围 0-20            → topic 字段去重计数（≥5=20,≥3=15,else=5）
      ③ 来源可信度 0-15          → get_source_tag("高") 占比（≥50%=15,≥25%=10）
      ④ 主题一致性 0-15          → BGE 余弦 <0.3 的 findings 占比（<10%=15,<30%=10）
    总分 ≥50 或 assess_round ≥3 → sufficient=True → 停止搜索
    （大纲移至 report_node 生成，不再参与 assess 评分）

  ◆ HITL 循环修复（clarify_node 早期返回）：
    hitl_scope 回放后，clarify_node 再次被调用
    → scope.suggested_dimensions 已存在（第一次设的）
    → 直接跳过 LLM，返回 need_scope=False
    → 防止 resume 后重新进入 hitl_scope 无限循环

  ◆ L1 写入守卫：
    memory_node 仅在 intent∈(deep_research, refine_section) 时写 L1
    simple_llm 路径只写 L0 + chat_history，不写长期记忆

  ◆ async/sync 分布：
    [async] resolve_context / intent_classifier / clarify / planner /
            context_mgr / assess / report / memory / simple_llm
    [sync]  search / scrape / dedup_rerank / hitl_scope
    LLM 调用统一使用 chat_async / chat_json_async（client.py）

═══════════════════════════════════════════════════════════════════════════
              路径差异对比表
═══════════════════════════════════════════════════════════════════════════

  ┌─────────────────┬────────────────┬────────────────┬────────────────┐
  │                 │ deep_research  │ refine_section │ simple_llm     │
  ├─────────────────┼────────────────┼────────────────┼────────────────┤
  │resolve_context  │      ✓         │      ✓         │      ✓         │
  │intent_classifier│      ✓         │      ✓         │      ✓         │
  │clarify          │      ✓         │      ✗         │      ✗         │
  │planner          │      ✓         │      ✓         │      ✗         │
  │search+scrape    │      ✓         │      ✓         │      ✗         │
  │context_mgr      │      ✓         │      ✓         │      ✗         │
  │dedup_rerank     │      ✓         │      ✓         │      ✗         │
  │assess           │  多轮回环      │    跳过        │      ✗         │
  │report           │      ✓         │      ✓         │      ✗         │
  │memory           │      ✓         │      ✓         │      ✓         │
  │                 │  (含 L1)       │  (含 L1)       │  (仅 L0)       │
  └─────────────────┴────────────────┴────────────────┴────────────────┘

═══════════════════════════════════════════════════════════════════════════
              每个节点详细说明
═══════════════════════════════════════════════════════════════════════════

┌─ 阶段一：查询预处理 ─────────────────────────────────────────────────────┐
│                                                                        │
│  ◆ resolve_context_node  [async]  (nodes.py:113-121)                   │
│     作用: 指代消解 + 口语→专业改写 + BGE 语义兜底。                       │
│           读取当前 session 最近 10 条任务的 L0 摘要（research_tasks 表），  │
│           调用 resolve_query() 做指代消解 + 改写                            │
│     输入: query, session_id                                              │
│           从 DB 读: get_recent_tasks_by_session(limit=10) → L0 摘要       │
│     核心: resolve_query(query, session_history)                          │
│           → LLM 消代+改写                                                │
│           → embed_query(query) vs embed_query(resolved) → 余弦 < 0.2    │
│             时回退用户原话                                                │
│     输出: resolved_query                                                 │
│                                                                        │
│  ◆ intent_classifier_node  [async]  (nodes.py:126-143)                  │
│     作用: 意图分类。LLM chat_json 将 resolved_query 分为 3 类。            │
│     输入: resolved_query, sections(已有章节), deep_search_count          │
│     核心: chat_json(_INTENT_PROMPT) → {intent, reason, section_name}     │
│           有效值: deep_research / refine_section / simple_llm            │
│           LLM 输出无效值 → 回退 simple_llm                                │
│     输出: intent, refine_section_name(仅 refine 时)                      │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘

┌─ 阶段二：范围确认 ───────────────────────────────────────────────────────┐
│                                                                        │
│  ◆ clarify_node  [async]  (nodes.py:148-165)                           │
│     作用: 判断查询是否需要用户补充研究范围（deep_research 路径优先执行）。      │
│     输入: resolved_query, scope(可能已有建议维度)                          │
│     核心: 早期返回: scope.suggested_dimensions 已有 → 跳过 LLM           │
│           正常: need_clarification(resolved_query)                      │
│             → chat_json(_CLARIFY_PROMPT)                                │
│             → {need_scope, suggested_dimensions(max4), details_to_add}  │
│     输出: need_scope, scope(dimensions+details+need_hitl)                │
│     路由: need_scope→hitl_scope; else→planner                            │
│     注意: refine_section 跳过此节点（直接 planner→search）                │
│           放在 planner 前，避免 planner 第一次调用被浪费                    │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘

┌─ 阶段三：研究规划 ───────────────────────────────────────────────────────┐
│                                                                        │
│  ◆ planner_node  [async]  (nodes.py:170-190)                           │
│     作用: 检索 L1 长期记忆 + 拆解子搜索查询。                              │
│     输入: resolved_query, findings(已有), user_supplement(可选)          │
│     核心:                                                               │
│       ① retrieve_l1(query, limit, min_score) → 合并到 findings          │
│          → fs_retriever.search(root="viking://user/memories")           │
│          → 每条记忆转为 {content, source_url, topic=name}                │
│       ② user_supplement 非空 → f"{query} {supplement}"                  │
│       ③ generate_sub_queries(resolved_query) → 3-5 sub_queries         │
│     输出: sub_queries, findings(含 L1 记忆)                              │
│     路由: → search（无条件，clarify 已在前面处理）                        │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘

┌─ 阶段四：搜索执行 ───────────────────────────────────────────────────────┐
│                                                                        │
│  ◆ search_node  [sync]  (nodes.py:209-283)                             │
│     作用: 多引擎搜索 + 回环去重。                                        │
│     输入: sub_queries, _searched_queries(已搜列表)                       │
│     核心逻辑:                                                           │
│       ① _searched_queries 过滤: 跳过已搜的子查询                        │
│       ② _try_engine(MetasoSearch) → 全部失败                            │
│          → _try_engine(DuckDuckGo) → 全部失败                           │
│          → 最终 fallback: 原始 query 搜 DuckDuckGo ×3                   │
│       ③ 多线程 (ThreadPoolExecutor, max 5) 并行搜子查询                  │
│          每 query ×3 条结果                                              │
│       ④ 过滤 scraped_pages 中已有 URL（避免下一轮重复抓取）               │
│       ⑤ deep_research 路径 → deep_search_count+1                        │
│     输出: search_results, search_all_failed, deep_search_count,         │
│            _searched_queries(追加本轮)                                   │
│     缓存: 无（搜索不缓存，但 scrape 缓存 URL）                             │
│     回环: 同 session 内 assess 路由到 search 时触发                      │
│                                                                        │
│  ◆ scrape_node  [sync]  (nodes.py:301-354)                             │
│     作用: 网页抓取 + 缓存（600s TTL）。                                   │
│     输入: search_results → 提取 URL 列表                                  │
│     核心:                                                               │
│       _scrape_cache 检查 → 命中直接返回                                  │
│       未命中 → Scraper.scrape(url)                                      │
│       抓取成功 → update_source_credibility(url, True)                    │
│                  → 缓存结果 → _scrape_cache[url] = (now, result)         │
│       抓取失败 → snippet 降级（有 snippet 时）                            │
│                  → 空内容 success=False（无 snippet 时）                  │
│       多线程 ThreadPoolExecutor(max_workers=5)                           │
│     输出: scraped_pages（按 URL 去重，_merge_pages reducer）              │
│                                                                        │
│  ◆ context_mgr_node  [async]  (nodes.py:359-410)                       │
│     作用: 提取关键要点 + 过滤无关/过短页面。                               │
│     输入: scraped_pages, page_summaries, resolved_query                 │
│     流程:                                                               │
│       ① 跳过 <100 字符 + 已处理的 URL                                   │
│       ② rerank(query, snippet+content[:300], top_k=1)                  │
│          得分 < 0.5 → 跳过（不相关）                                     │
│       ③ batch_brief_from_pages() → LLM 批量提取 key_points             │
│     输出: page_summaries（追加已有列表）                                  │
│                                                                        │
│  ◆ dedup_rerank_node  [sync]  (nodes.py:415-464)                       │
│     作用: BGE 语义去重 + CrossEncoder 增量打分。                          │
│     输入: page_summaries, findings(已有), _findings_embeddings(缓存)    │
│     流程:                                                               │
│       ① embed_text(batch) → 新 findings 嵌入                            │
│       ② 新 findings vs _findings_embeddings → max_cosine_similarity    │
│          ≥ SEMANTIC_DUP_THRESHOLD(0.85) → 丢弃（重复）                  │
│       ③ 仅新 findings 过 CrossEncoder: rank_findings(top_k=15)         │
│          score ≥ 0.5 保留                                                │
│       ④ 合并旧 findings（保留上轮 score）+ 新 findings                   │
│       ⑤ 排序截取 top 15                                                │
│     输出: findings, _findings_embeddings(追加)                           │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘

┌─ 阶段五：覆盖度评估 ─────────────────────────────────────────────────────┐
│                                                                        │
│  ◆ assess_node  [async]  (nodes.py:469-545)                            │
│     作用: 4 维度评估覆盖度。大纲已移至 report_node。                        │
│     输入: findings(前15×300字), resolved_query                          │
│     核心:                                                               │
│       LLM 部分 (1 维度):                                                │
│         ① query_match 0-30  从[30,20,10,5]选                           │
│       代码计算 (3 维度):                                                │
│         ② scope 0-20  topic 去重数 ≥5=20 ≥3=15 else=5                 │
│         ③ credibility 0-15  tag("高")占比 ≥50%=15 ≥25%=10 else=5      │
│         ④ consistency 0-15  BGE余弦<0.3占比 <10%=15 <30%=10 else=5    │
│       总分 = ①+②+③+④ ≥50 或 round≥3 → sufficient=True                 │
│       满分 80（去掉了旧大纲维度 20 分）                                   │
│     注意: 不生成大纲/gaps/new_sub_queries                                  │
│           回环复用 planner_node 的原始 sub_queries（_searched_queries 去重）│
│           冲突检测已移除（位置不对 + 价值低）                               │
│     输出: coverage_score, score_detail, sufficient,                     │
│            assess_round+1, _llm_fallback                                │
│     路由: route_assess 决定 → search / report                           │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘

┌─ 阶段六：报告生成 ───────────────────────────────────────────────────────┐
│                                                                        │
│  ◆ report_node  [async]  (nodes.py:627-695)                            │
│     作用: 生成大纲 + 流式报告（覆盖全路径）。                               │
│     输入: intent, query, findings, outline, sections, report(已有)     │
│     核心:                                                               │
│       refine_section 无结果 → chat_async 基于已有知识回答                 │
│       _llm_fallback 无结果 → chat_async 直接回答                        │
│       refine_section 有结果 → 单章大纲 + generate_report_stream()       │
│       deep_research    有结果 → state 取 outline，无则 generate_outline │
│                                 → generate_report_stream()              │
│       流式推 token 到前端 SSE（stream_callback_var）                     │
│       [1][2] 格式引用标注 + 参考资料块                                   │
│       累积报告 = 已有 + "\n\n---\n\n" + 本轮                              │
│     输出: report(累积), turn_report(本轮), sections, outline,          │
│            _report_streamed                                             │
│     注意: synthesize_node 功能已合并至此节点                               │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘

┌─ 阶段七：记忆持久化 ─────────────────────────────────────────────────────┐
│                                                                        │
│  ◆ memory_node  [async]  (nodes.py:737-762)                            │
│     作用: 两阶段持久化 —— L0(L0压缩+写表) + L1(长期记忆提取，带 intent 守卫) │
│     输入: task_id, session_id, query, report, turn_report, intent      │
│     阶段 1（research_tasks 表 + chat_history 表）：                      │
│       _compress_l0(query, report) → ≤150 字摘要                        │
│       update_task(task_id, status=completed, report, l0_summary)       │
│       insert_chat_message(session_id, assistant, turn_report)          │
│     阶段 2（L1 → fs_nodes 表，仅 deep_research/refine_section）：        │
│       仅 intent∈(deep_research, refine_section) 时执行:                │
│       _write_long_term_memory(session_id, task_id, report)             │
│         → await update_memory(session_id, task_id, report)             │
│           → extract_knowledge(report) → LLM 提取 3-5 条知识             │
│           → _search_similar → 双阈值去重 (UPDATE/APPEND/ADD)           │
│         _names_overlap(name) 校验防止跨主题合并                          │
│         （仅报告 ≥200 字符时执行）                                       │
│       simple_llm 路径跳过 L1（不写入长期记忆）                            │
│     注意: memory_llm_node 功能已合并至此节点                              │
│     输出: status="completed" → END                                      │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘

┌─ simple_llm 路径专用节点 ───────────────────────────────────────────────┐
│                                                                        │
│  ◆ simple_llm_node  [async]  (nodes.py:776-792)                        │
│     作用: 简单 LLM 回答。不搜索、不写 L1。                                │
│     触发: intent="simple_llm"                                           │
│     输入: query, session_id                                             │
│     历史: get_recent_tasks_by_session(limit=5) → L0 摘要列表            │
│     核心: chat_async(_SIMPLE_LLM_PROMPT.format(history, query))         │
│           prompt 不传 report，只传 L0 摘要                               │
│     输出: report(LLM 回复), status="completed" → memory_node           │
│     适用: 问候/自我介绍/助手功能/简单问答/总结/改写                       │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════
              版本变更记录
═══════════════════════════════════════════════════════════════════════════

  clarify ↔ planner 重排:
    · 顺序: planner→clarify  →  clarify→planner
    · 省去第一次 planner 调用被 clarify 浪费的问题
    · route_by_intent: deep_research→clarify, refine_section→planner
    · route_after_planner 删除（planner 后无条件 search）
    · clarify lambda 目标: search → planner

  方案 A 节点合并（check_history→planner, synthesize→report, memory_llm→memory）:
     · 17 functional → 12 functional（+1 HITL = 13 总节点）
    · route_by_intent: check_history → planner
    · route_after_rerank: synthesize → report
    · route_assess: synthesize → report
    · turn_topic 字段删除（死字段）

  assess 重写（去大纲/gaps/new_sub_queries）:
    · _ASSESS_PROMPT 从 ~70 行 → ~25 行
    · 评估维度 5→4（删 outline_covered 20 分）
    · 满分 90→80，threshold 60→50
    · 大纲生成移至 report_node（已存在 fallback）
    · 回环复用 planner 原始 sub_queries
