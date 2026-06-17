"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "/api";
type Json = Record<string, any>;
type Tab = "play" | "actors" | "settings" | "memory" | "rules";
type Message = { role: "player" | "dm"; text: string; details?: string };

async function call(path: string, options?: RequestInit) {
  const isForm = options?.body instanceof FormData;
  const response = await fetch(`${API}${path}`, {
    headers: { ...(isForm ? {} : { "Content-Type": "application/json" }), ...(options?.headers || {}) },
    ...options,
  });
  if (!response.ok) throw new Error((await response.text()) || `${response.status} ${response.statusText}`);
  if (response.status === 204) return null;
  return response.json();
}

function short(value: unknown, length = 180) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

export default function Home() {
  const [tab, setTab] = useState<Tab>("play");
  const [campaigns, setCampaigns] = useState<Json[]>([]);
  const [campaignId, setCampaignId] = useState("campaign_001");
  const [campaignStatus, setCampaignStatus] = useState<Json>({});
  const [characters, setCharacters] = useState<Json[]>([]);
  const [actors, setActors] = useState<Json[]>([]);
  const [characterId, setCharacterId] = useState("");
  const [settings, setSettings] = useState<Json[]>([]);
  const [drafts, setDrafts] = useState<Json[]>([]);
  const [memories, setMemories] = useState<Json[]>([]);
  const [threads, setThreads] = useState<Json[]>([]);
  const [events, setEvents] = useState<Json[]>([]);
  const [conflicts, setConflicts] = useState<Json[]>([]);
  const [messages, setMessages] = useState<Message[]>([
    { role: "dm", text: "DM 控制台已就绪。选择战役后，可以开始游玩或进入战役编辑模式。" },
  ]);
  const [input, setInput] = useState("我观察周围有什么异常。");
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<Json[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("正在连接本地 DM Agent…");
  const [actorName, setActorName] = useState("");
  const [actorKind, setActorKind] = useState("npc");
  const [actorPurpose, setActorPurpose] = useState("");
  const [actorInstructions, setActorInstructions] = useState("");
  const [qqDrafts, setQqDrafts] = useState<Record<string, string>>({});

  const character = characters.find((item) => item.id === characterId) || characters[0];
  const hp = character?.data?.combat;
  const hpPercent = useMemo(() => hp ? Math.max(0, Math.min(100, Math.round((hp.current_hp / hp.max_hp) * 100))) : 0, [hp]);
  const pendingDrafts = drafts.filter((item) => item.status === "pending");

  const refreshCampaign = useCallback(async (id: string) => {
    const [status, chars, actorList, published, draftList, memoryList, threadList, eventList, conflictList] = await Promise.all([
      call(`/campaigns/${id}/status`),
      call(`/campaigns/${id}/characters`),
      call(`/campaigns/${id}/actors`),
      call(`/campaigns/${id}/settings`),
      call(`/campaigns/${id}/setting-drafts`),
      call(`/campaigns/${id}/memories?limit=20`),
      call(`/campaigns/${id}/threads`),
      call(`/campaigns/${id}/events`),
      call(`/campaigns/${id}/settings/conflicts`),
    ]);
    setCampaignStatus(status); setCharacters(chars); setActors(actorList); setSettings(published); setDrafts(draftList);
    setMemories(memoryList); setThreads(threadList); setEvents(eventList.slice(0, 20)); setConflicts(conflictList);
    setCharacterId((old) => chars.some((item: Json) => item.id === old) ? old : chars[0]?.id || "");
  }, []);

  const refreshAll = useCallback(async () => {
    try {
      const list = await call("/campaigns");
      setCampaigns(list);
      const chosen = list.find((item: Json) => item.id === campaignId) || list[0];
      if (chosen) {
        setCampaignId(chosen.id);
        await call(`/napcat/active-campaign/${chosen.id}`, { method: "PUT" });
        await refreshCampaign(chosen.id);
        setNotice(`已连接 ${API}`);
      } else setNotice("尚无战役，请先初始化示例数据");
    } catch (error) {
      setNotice(`连接失败：${String(error)}`);
    }
  }, [campaignId, refreshCampaign]);

  useEffect(() => { refreshAll(); }, []);

  async function action(label: string, fn: () => Promise<any>) {
    setBusy(true);
    try {
      const result = await fn();
      await refreshCampaign(campaignId);
      setNotice(label);
      return result;
    } catch (error) {
      setNotice(`${label}失败：${String(error)}`);
    } finally { setBusy(false); }
  }

  async function bootstrap() {
    await action("示例战役已初始化", async () => {
      await call("/demo/bootstrap", { method: "POST" });
      await Promise.all([call("/ingest/compendium", { method: "POST" }), call("/ingest/rules", { method: "POST" })]);
      await refreshAll();
    });
  }

  async function send(event?: FormEvent, command?: string) {
    event?.preventDefault();
    const text = (command || input).trim();
    if (!text) return;
    setMessages((old) => [...old, { role: "player", text }]);
    setInput("");
    setBusy(true);
    try {
      let message = text;
      if (files.length) {
        const allowedExts = [".pdf", ".docx", ".doc", ".txt", ".md", ".json", ".csv", ".tsv",
          ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".xlsx", ".xlsm", ".pptx", ".html", ".htm",
          ".zip", ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4", ".mov", ".mkv", ".avi", ".webm"];
        const maxBytes = 20 * 1024 * 1024; // 20 MB
        let totalSize = 0;
        for (const file of files) {
          const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
          if (!allowedExts.includes(ext)) {
            throw new Error(`不支持的文件类型: ${file.name}`);
          }
          totalSize += file.size;
        }
        if (totalSize > maxBytes) throw new Error("附件总大小超过 20MB 限制");
        const form = new FormData();
        files.forEach((file) => form.append("files", file));
        const parsed = await call("/parse/files", { method: "POST", body: form });
        message += `\n\n附件解析内容：\n${parsed.content || JSON.stringify(parsed)}`;
      }
      const result = await call(`/chat/${campaignId}`, {
        method: "POST",
        body: JSON.stringify({ session_id: "webui", character_id: character?.id, message }),
      });
      const details = [
        result.kind && `类型: ${result.kind}`,
        ...(result.rolls || []).map((item: Json) => `${item.formula} = ${item.total}`),
        ...(result.state_changes || []).map((item: Json) => item.type),
      ].filter(Boolean).join(" · ");
      setMessages((old) => [...old, { role: "dm", text: result.narration, details }]);
      setFiles([]);
      await refreshCampaign(campaignId);
      setNotice("消息处理完成");
    } catch (error) {
      setMessages((old) => [...old, { role: "dm", text: `处理失败：${String(error)}` }]);
    } finally { setBusy(false); }
  }

  async function search(kind: "setting" | "memory" | "rule" | "spell") {
    if (!query.trim()) return;
    const paths = {
      setting: `/campaigns/${campaignId}/settings?query=${encodeURIComponent(query)}`,
      memory: `/campaigns/${campaignId}/memories?query=${encodeURIComponent(query)}`,
      rule: `/rules/search?query=${encodeURIComponent(query)}`,
      spell: `/spells?query=${encodeURIComponent(query)}`,
    };
    await action("检索完成", async () => setSearchResults(await call(paths[kind])));
  }

  async function createActor(event: FormEvent) {
    event.preventDefault();
    if (!actorName.trim()) return;
    await action("DM 角色卡已建立", async () => {
      await call("/characters/build", {
        method: "POST",
        body: JSON.stringify({
          campaign_id: campaignId, player_name: "DM", character_name: actorName.trim(),
          actor_type: actorKind, class_name: actorKind === "monster" ? "Fighter" : "Rogue",
          abilities: { str: 10, dex: 10, con: 10, int: 10, wis: 10, cha: 10 },
          roleplay: { roleplay_instructions: actorInstructions, public_persona: "", secrets: [], goals: [] },
          story_role: { purpose: actorPurpose, planned_actions: [], triggers: [] },
          encounter: { present: false, scene: "" },
        }),
      });
      setActorName(""); setActorPurpose(""); setActorInstructions("");
    });
  }

  async function switchCampaign(id: string) {
    setCampaignId(id);
    setBusy(true);
    try {
      await call(`/napcat/active-campaign/${id}`, { method: "PUT" });
      await refreshCampaign(id);
      setNotice("已切换战役与 QQ 角色绑定上下文");
    } catch (error) {
      setNotice(`切换战役失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function updateCharacterQq(character: Json) {
    const text = qqDrafts[character.id] ?? (character.data?.integrations?.qq_user_ids || []).join(", ");
    const qq_user_ids = text.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean);
    await action("角色 QQ 绑定已更新", () => call(`/characters/${character.id}/qq-bindings`, {
      method: "PATCH", body: JSON.stringify({ qq_user_ids }),
    }));
  }

  const modes: Record<string, string> = { free: "自由扮演", turn_based: "回合制", combat: "战斗", campaign_edit: "战役编辑" };
  const quick = [
    ["/status", "状态"], ["/turns", "进入回合"], ["/combat", "进入战斗"], ["/endcombat", "结束战斗"],
    ["/editcampaign", "编辑战役"], ["/drafts", "查看草稿"], ["/publishsettings", "发布设定"], ["/exitedit", "退出编辑"],
  ];

  return (
    <main>
      <header>
        <div><span className="eyebrow">LOCAL-FIRST CAMPAIGN OS</span><h1>暮色编年史</h1></div>
        <div className="header-actions">
          <select value={campaignId} onChange={(event) => switchCampaign(event.target.value)}>
            {campaigns.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
          </select>
          <span className="status"><i />{notice}</span>
          <button className="secondary" onClick={refreshAll} disabled={busy}>刷新</button>
          <button onClick={bootstrap} disabled={busy}>初始化</button>
        </div>
      </header>

      <nav>
        {([["play", "游玩控制台"], ["actors", "NPC / 怪物"], ["settings", `战役设定 ${pendingDrafts.length ? `(${pendingDrafts.length})` : ""}`], ["memory", "记忆与剧情"], ["rules", "规则与角色"]] as [Tab, string][]).map(([id, label]) => (
          <button className={tab === id ? "active" : ""} onClick={() => setTab(id)} key={id}>{label}</button>
        ))}
      </nav>

      <section className="overview">
        <div><small>运行模式</small><strong>{modes[campaignStatus.runtime_mode] || campaignStatus.runtime_mode || "-"}</strong></div>
        <div><small>战役状态</small><strong>{campaignStatus.status || "-"}</strong></div>
        <div><small>玩法</small><strong>{campaignStatus.play_style === "dice_assistant" ? "骰娘辅助" : "战役叙事"}</strong></div>
        <div><small>已发布设定</small><strong>{settings.length}</strong></div>
        <div><small>待审草稿</small><strong>{pendingDrafts.length}</strong></div>
        <div><small>在场 DM 角色</small><strong>{actors.filter((item) => item.data?.basic?.actor_type !== "player" && item.data?.encounter?.present !== false).length}</strong></div>
      </section>

      {tab === "play" && <section className="workspace play-grid">
        <aside className="panel character-panel">
          <p className="label">当前角色</p>
          <select value={character?.id || ""} onChange={(event) => setCharacterId(event.target.value)}>
            {characters.map((item) => <option key={item.id} value={item.id}>{item.character_name}</option>)}
          </select>
          <div className="portrait">{character?.character_name?.slice(0, 1) || "?"}</div>
          <h2>{character?.character_name || "尚未创建角色"}</h2>
          <div className="stat-line"><span>生命值</span><strong>{hp?.current_hp ?? "-"}/{hp?.max_hp ?? "-"}</strong></div>
          <div className="hp-track"><span style={{ width: `${hpPercent}%` }} /></div>
          <div className="stats">
            <div><small>AC</small><strong>{hp?.armor_class ?? "-"}</strong></div>
            <div><small>等级</small><strong>{character?.data?.basic?.level ?? "-"}</strong></div>
            <div><small>版本</small><strong>{character?.version ?? "-"}</strong></div>
          </div>
          <p className="label section-label">快捷控制</p>
          <div className="command-grid">{quick.map(([cmd, label]) => <button className="chip" key={cmd} onClick={() => send(undefined, cmd)}>{label}</button>)}</div>
          <p className="label section-label">玩法模式</p>
          <div className="command-grid"><button className="chip" onClick={() => send(undefined, "/骰娘")}>进入骰娘</button><button className="chip" onClick={() => send(undefined, "/退出骰娘")}>退出骰娘</button></div>
        </aside>

        <section className="panel chat-panel">
          <div className="panel-head"><div><p className="label">DM 对话</p><h2>{campaigns.find((item) => item.id === campaignId)?.name || "当前战役"}</h2></div><span className="mode">{modes[campaignStatus.runtime_mode] || "-"}</span></div>
          <div className="messages">{messages.map((message, index) => <article className={message.role} key={index}><span>{message.role === "dm" ? "DM AGENT" : character?.character_name || "DM"}</span><p>{message.text}</p>{message.details && <pre>{message.details}</pre>}</article>)}</div>
          <form onSubmit={send}>
            <textarea value={input} onChange={(event) => setInput(event.target.value)} placeholder={campaignStatus.runtime_mode === "campaign_edit" ? "与 Agent 讨论和修改战役设定…" : "描述行动或输入命令…"} />
            <label className="file-button">附件 {files.length || ""}<input type="file" multiple onChange={(event) => setFiles(Array.from(event.target.files || []))} /></label>
            <button disabled={busy}>{busy ? "处理中…" : "发送"}</button>
          </form>
        </section>

        <aside className="panel feed">
          <p className="label">最近事件</p>
          {events.slice(0, 8).map((item) => <div className="feed-item" key={item.id}><small>{item.event_type}</small><p>{short(item.content, 110)}</p></div>)}
          {!events.length && <p className="muted">还没有事件。</p>}
        </aside>
      </section>}

      {tab === "actors" && <section className="workspace two-col">
        <section className="panel content-panel">
          <div className="panel-head"><div><p className="label">DM ACTORS</p><h2>NPC 与怪物角色卡</h2></div></div>
          <div className="actor-grid">{actors.filter((item) => item.data?.basic?.actor_type !== "player").map((item) => {
            const present = item.data?.encounter?.present !== false;
            return <article className="actor-card" key={item.id}>
              <div className="actor-title"><div className="mini-portrait">{item.character_name?.slice(0, 1)}</div><div><small>{item.data?.basic?.actor_type} · v{item.version}</small><h3>{item.character_name}</h3></div><span className={present ? "presence on" : "presence"}>{present ? "在场" : "离场"}</span></div>
              <p><b>剧情职责：</b>{item.data?.story_role?.purpose || "尚未填写"}</p>
              <p><b>扮演指引：</b>{item.data?.roleplay?.roleplay_instructions || item.data?.roleplay?.combat_behavior || "尚未填写"}</p>
              <p><b>计划行动：</b>{(item.data?.story_role?.planned_actions || []).join("；") || "无"}</p>
              <p><b>战斗：</b>HP {item.data?.combat?.current_hp ?? "-"}/{item.data?.combat?.max_hp ?? "-"} · AC {item.data?.combat?.armor_class ?? "-"}</p>
              <button className={present ? "danger" : ""} onClick={() => action(present ? "角色已离场" : "角色已加入场景", () => call(`/characters/${item.id}/presence`, { method: "PATCH", body: JSON.stringify({ present: !present, scene: "" }) }))}>{present ? "移出当前场景" : "加入当前场景"}</button>
            </article>;
          })}</div>
        </section>
        <aside className="panel content-panel">
          <p className="label">CREATE DM ACTOR</p><h2>快速建立角色卡</h2>
          <form className="stack-form" onSubmit={createActor}>
            <label>类型<select value={actorKind} onChange={(e) => setActorKind(e.target.value)}><option value="npc">NPC</option><option value="monster">怪物</option></select></label>
            <label>名称<input value={actorName} onChange={(e) => setActorName(e.target.value)} placeholder="例如：米拉 / 食人魔守卫"/></label>
            <label>剧情职责<textarea value={actorPurpose} onChange={(e) => setActorPurpose(e.target.value)} placeholder="在设计好的剧情中要做什么"/></label>
            <label>DM 扮演指引<textarea value={actorInstructions} onChange={(e) => setActorInstructions(e.target.value)} placeholder="语气、动机、秘密、战斗行为等"/></label>
            <button disabled={busy || !actorName.trim()}>建立角色卡</button>
          </form>
          <p className="muted">建立后可通过角色卡 API 完整维护属性、技能、法术、物品、秘密、关系、触发条件与计划行动。</p>
        </aside>
      </section>}

      {tab === "settings" && <section className="workspace two-col">
        <section className="panel content-panel">
          <div className="panel-head"><div><p className="label">CANON</p><h2>已发布战役设定</h2></div><div className="search"><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="语义搜索设定"/><button onClick={() => search("setting")}>搜索</button></div></div>
          <div className="card-grid">{(searchResults.length ? searchResults : settings).map((item) => <article className="card" key={item.id}><small>{item.category} · v{item.version}</small><h3>{item.name}</h3><p>{item.summary || short(item.content)}</p><div className="tags">{(item.tags || []).map((tag: string) => <span key={tag}>{tag}</span>)}</div></article>)}</div>
        </section>
        <aside className="panel content-panel">
          <div className="panel-head"><div><p className="label">REVIEW QUEUE</p><h2>待审草稿</h2></div></div>
          <div className="toolbar">
            <button onClick={() => action("草稿已发布", () => call(`/campaigns/${campaignId}/setting-drafts/publish`, { method: "POST" }))}>全部发布</button>
            <button className="secondary" onClick={() => action("已撤销最近草稿", () => call(`/campaigns/${campaignId}/setting-drafts/undo`, { method: "POST" }))}>撤销最近</button>
            <button className="danger" onClick={() => action("草稿已放弃", () => call(`/campaigns/${campaignId}/setting-drafts`, { method: "DELETE" }))}>全部放弃</button>
          </div>
          {pendingDrafts.map((item) => <article className="draft" key={item.id}><small>{item.operation} · {item.category}</small><h3>{item.name}</h3><p>{item.reason || "对话式编辑草稿"}</p><pre>{short(item.proposal, 360)}</pre></article>)}
          {!pendingDrafts.length && <p className="muted">没有待审草稿。通过“编辑战役”进入对话式编辑模式。</p>}
          {!!conflicts.length && <><p className="label section-label">冲突建议</p>{conflicts.map((item) => <div className="warning" key={`${item.setting_id}-${item.event_id}`}>{item.setting_name}: {item.reason}</div>)}</>}
        </aside>
      </section>}

      {tab === "memory" && <section className="workspace three-col">
        <section className="panel content-panel"><div className="panel-head"><div><p className="label">RECALL</p><h2>结构化记忆</h2></div><div className="search"><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索记忆"/><button onClick={() => search("memory")}>召回</button></div></div>{(searchResults.length ? searchResults : memories).map((item) => <article className="list-card" key={item.id}><small>{item.memory_type || item.type} · 重要度 {item.importance}</small><p>{item.content}</p></article>)}</section>
        <section className="panel content-panel"><p className="label">OPEN THREADS</p><h2>开放剧情线</h2>{threads.map((item) => <article className="list-card" key={item.id}><small>{item.status} · 优先级 {item.priority}</small><h3>{item.title}</h3><p>{item.description}</p></article>)}</section>
        <section className="panel content-panel"><p className="label">ENTITIES</p><h2>记忆工具</h2><button onClick={() => action("历史记忆回填完成", () => call(`/campaigns/${campaignId}/memories/backfill`, { method: "POST" }))}>回填历史事件</button><button className="secondary block" onClick={() => action("战役总结已生成", () => call(`/campaigns/${campaignId}/summaries?session_id=webui`, { method: "POST" }))}>生成战役总结</button><p className="muted">Agent 会在每次剧情事件后提取事实、实体与开放剧情线，并在后续推理中召回。</p></section>
      </section>}

      {tab === "rules" && <section className="workspace two-col">
        <section className="panel content-panel">
          <div className="panel-head"><div><p className="label">BGE-M3 + CATALOG</p><h2>规则与法术检索</h2></div></div>
          <div className="search large"><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="输入规则、法术名或关键词"/><button onClick={() => search("rule")}>查规则</button><button className="secondary" onClick={() => search("spell")}>查法术</button></div>
          {searchResults.map((item, index) => <article className="list-card" key={item.id || index}><small>{item.source || item.level || item.entry_type || "RESULT"}</small><h3>{item.name || item.section || item.chapter}</h3><p>{item.description || item.chunk_text || short(item, 420)}</p></article>)}
        </section>
        <aside className="panel content-panel"><p className="label">CHARACTER SHEETS</p><h2>战役角色</h2>{characters.map((item) => <article className="character-card" key={item.id}><div className="mini-portrait">{item.character_name?.slice(0, 1)}</div><div><h3>{item.character_name}</h3><p>{item.player_name || "DM"} · v{item.version}</p><span>HP {item.data?.combat?.current_hp ?? "-"}/{item.data?.combat?.max_hp ?? "-"} · AC {item.data?.combat?.armor_class ?? "-"}</span><label>绑定 QQ（逗号分隔）<input value={qqDrafts[item.id] ?? (item.data?.integrations?.qq_user_ids || []).join(", ")} onChange={(event) => setQqDrafts((old) => ({ ...old, [item.id]: event.target.value }))}/></label><button onClick={() => updateCharacterQq(item)}>更新绑定</button><button className="danger" onClick={() => action("角色与绑定已删除", () => call(`/characters/${item.id}`, { method: "DELETE" }))}>删除角色</button></div></article>)}</aside>
      </section>}
    </main>
  );
}
