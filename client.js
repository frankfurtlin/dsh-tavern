/**
 * dsh-story-between —— client 半（浏览器）。
 *
 * 手写 ModuleLoader 工厂，不经打包：只依赖宿主已提供的 react / react/jsx-runtime，
 * 用 h() 直接构造元素。做三件事：
 *   1. 在侧栏「插件」组的 panellist 座位注册一行「故事之间」（order 24，落在
 *      紫微斗数(20) 与 Skill Center(30) 之间）。
 *   2. 在 main 座位注册 keyed 面板页：同源 iframe 指向宿主反代的 /story/，
 *      工具条提供刷新 / 新窗口 / 让 DSH 续写 / 角色扮演 / 续写要求输入。
 *   3. P2 桥：按 docs/contract.md §3 的 postMessage 协议向 iframe 要当前故事
 *      上下文，组装 prompt 后新开 DSH 会话交给 Agent（并预填编辑器 + 剪贴板兜底）。
 */
window.__ModuleLoader__.load({
  id: "dsh-story-between",
  factory: function (require) {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });

    var react = require("react");
    var h = react.createElement;

    /** 侧栏行 id —— 同时是 main 座位的 key 与图标上的 data 锚点。 */
    var PANEL_ID = "story";
    /** 侧栏顺序：紫微斗数 = 20，Skill Center = 30。 */
    var PANEL_ORDER = 24;

    var COLORS = {
      border: "color-mix(in srgb, currentColor 18%, transparent)",
      muted: "color-mix(in srgb, currentColor 58%, transparent)",
      chip: "color-mix(in srgb, currentColor 8%, transparent)",
    };

    /** P2 消息类型，必须与 docs/contract.md §3 完全一致。 */
    var MSG = {
      ping: "dsh-story:ping",
      pong: "dsh-story:pong",
      state: "dsh-story:state",
      contextRequest: "dsh-story:context-request",
      context: "dsh-story:context",
      // 主题同步是契约之外的附加消息：应用侧愿意听就跟着切主题，不听也无害。
      theme: "dsh-story:theme",
    };

    /** 向 iframe 要上下文的超时：应用侧要读 store、拼摘要，给足 8 秒。 */
    var CONTEXT_TIMEOUT_MS = 8000;

    /**
     * iframe 位置记忆：DSH 切走面板会卸载 iframe，应用回来时默认停在 /explore，
     * 故事上下文就丢了。这里记住「最后一次有效的应用路由」，重新挂载面板时把
     * iframe 恢复到那里。模块作用域内存缓存为主（SWR 式，够用且无磁盘负担），
     * 另外镜像一份到 sessionStorage，让 DSH 页面刷新后也能接上。
     */
    var REMEMBERED_PATH_KEY = "dsh-story:last-app-path";
    var rememberedAppPath = "";

    /** 只接受站内绝对路径；排除 `//` 开头的协议相对地址，避免拼出外站地址。 */
    function isUsableAppPath(path) {
      return typeof path === "string" && path.charAt(0) === "/" && path.charAt(1) !== "/";
    }

    function loadRememberedPath() {
      try {
        var stored = window.sessionStorage.getItem(REMEMBERED_PATH_KEY);
        return typeof stored === "string" ? stored : "";
      } catch (error) {
        return "";
      }
    }

    function storeRememberedPath(path) {
      rememberedAppPath = path;
      try {
        window.sessionStorage.setItem(REMEMBERED_PATH_KEY, path);
      } catch (error) {}
    }

    function forgetRememberedPath() {
      rememberedAppPath = "";
      try {
        window.sessionStorage.removeItem(REMEMBERED_PATH_KEY);
      } catch (error) {}
    }

    /**
     * 应用路由 → iframe 地址：`story/` + 去掉前导斜杠的 path。
     * 无可用记忆时返回 null，由调用方退回应用首页。
     */
    function frameHrefForAppPath(appPath) {
      if (!isUsableAppPath(appPath)) return null;
      var rest = appPath.replace(/^\/+/, "");
      if (!rest) return null;
      try {
        return new URL("story/" + rest, document.baseURI).href;
      } catch (error) {
        return null;
      }
    }

    // 模块加载时把上次的位置读回内存（DSH 页面刷新后仍能恢复）。
    rememberedAppPath = loadRememberedPath();
    if (!isUsableAppPath(rememberedAppPath)) rememberedAppPath = "";

    /** 读宿主（DSH 外壳）当前配色：外壳用 color-scheme 表达浅色/深色。 */
    function hostTheme() {
      try {
        var scheme = getComputedStyle(document.documentElement).colorScheme || "";
        return scheme.indexOf("dark") !== -1 ? "dark" : "light";
      } catch (error) {
        return "light";
      }
    }

    /** 上下文里「最近正文」的统一兜底。 */
    function recentTextOf(context) {
      var text = context && typeof context.recentText === "string" ? context.recentText.trim() : "";
      return text || "（应用没有提供最近正文）";
    }

    /** 角色列表 → prompt 里的几行。 */
    function characterLines(characters) {
      var list = characters || [];
      if (!list.length) return ["（应用没有提供角色信息）"];
      return list.map(function (item) {
        var text = "- " + (item.name || "未命名角色");
        if (item.identity) text += "（" + item.identity + "）";
        if (item.speech) text += "，说话口吻：" + item.speech;
        return text;
      });
    }

    /** 作品/章节标题。 */
    function contextLabel(context) {
      var world = (context && context.worldTitle) || "未命名作品";
      var line = context && context.lineTitle ? " · " + context.lineTitle : "";
      return world + line;
    }

    /**
     * 续写 prompt：作品/章节/模式/角色/最近正文 + 固定续写要求 + 用户额外要求。
     * @param {object} context iframe 回传的上下文
     * @param {string} extra 用户在工具条里填的额外要求
     */
    function buildContinuePrompt(context, extra) {
      var lines = [
        "请帮我接着下面的故事往下续写。",
        "",
        "【作品】" + ((context && context.worldTitle) || "未命名作品"),
        "【章节】" + ((context && context.lineTitle) || "未命名章节"),
      ];
      if (context && context.mode) lines.push("【模式】" + context.mode);
      if (context && context.appPath) lines.push("【页面】" + context.appPath);
      lines.push("");
      lines.push("【角色】");
      lines = lines.concat(characterLines(context && context.characters));
      lines.push("");
      lines.push("【最近正文】");
      lines.push(recentTextOf(context));
      lines.push("");
      lines.push("【要求】");
      lines.push("请基于以上内容续写下一段（400–600 字，保持文风与人物口吻）。");
      if (extra && extra.trim()) {
        lines.push("");
        lines.push("【我的额外要求】");
        lines.push(extra.trim());
      }
      return lines.join("\n");
    }

    /**
     * 角色扮演 prompt：指定角色 + 最近情境 + 第一人称即兴演绎 + 用户额外要求。
     * @param {object} context iframe 回传的上下文
     * @param {object} target 扮演对象（{name, identity, speech}）
     * @param {string} extra 用户在工具条里填的额外要求
     */
    function buildRoleplayPrompt(context, target, extra) {
      var name = (target && target.name) || "最近活跃角色";
      var role = "【你要扮演】" + name;
      if (target && target.identity) role += "（" + target.identity + "）";
      if (target && target.speech) role += "，说话口吻：" + target.speech;
      var lines = [
        "请以角色扮演的方式，接着下面的故事即兴演绎。",
        "",
        "【作品】" + contextLabel(context),
        "【模式】" + ((context && context.mode) || "角色扮演"),
        "",
        "【角色】",
      ];
      lines = lines.concat(characterLines(context && context.characters));
      lines.push("");
      lines.push(role);
      lines.push("");
      lines.push("【最近情境】");
      lines.push(recentTextOf(context));
      lines.push("");
      lines.push("【要求】");
      lines.push("以 " + name + " 的第一人称即兴演绎，保持其身份、认知与说话口吻。");
      lines.push("承接最近情境往下写 300–500 字，不要复述原文，也不要写成旁白。");
      if (extra && extra.trim()) {
        lines.push("");
        lines.push("【我的额外要求】");
        lines.push(extra.trim());
      }
      return lines.join("\n");
    }

    /**
     * 选角色扮演对象：
     *   1. 用户在额外要求里写 `@角色名` → 按名字匹配；
     *   2. 契约 §3 的 context.activeCharacter（最近活跃角色）→ 按名字匹配；
     *   3. 都没有 → 退化取 characters 末位（应用侧通常把新近角色排在后面）。
     */
    function pickRoleplayTarget(context, extra) {
      var characters = (context && context.characters) || [];
      var match = String(extra || "").match(/@([^\s，,。；;：:]+)/);
      var wanted = match ? match[1] : "";
      if (wanted) {
        for (var i = 0; i < characters.length; i += 1) {
          var name = String(characters[i].name || "");
          if (name && (name === wanted || name.indexOf(wanted) !== -1)) return characters[i];
        }
      }
      var active =
        context && typeof context.activeCharacter === "string"
          ? context.activeCharacter.trim()
          : "";
      if (active) {
        for (var j = 0; j < characters.length; j += 1) {
          if (String(characters[j].name || "") === active) return characters[j];
        }
        // 给了名字但 characters 里没有：仍按这个名字组 prompt。
        return { name: active, identity: "", speech: "" };
      }
      if (characters.length) return characters[characters.length - 1];
      return null;
    }

    /** 把额外要求里的 `@角色名` 去掉（它只用于指定扮演对象，不该原样进 prompt）。 */
    function stripRoleToken(extra) {
      return String(extra || "")
        .replace(/@[^\s，,。；;：:]+/g, "")
        .replace(/[ \t]+/g, " ")
        .trim();
    }

    /** 侧栏行图标：摊开的书页 + 笔触。 */
    function StoryPanelIcon(props) {
      var size = (props && props.size) || 16;
      return h(
        "svg",
        {
          "data-dsh-panel-entry": PANEL_ID,
          viewBox: "0 0 16 16",
          width: size,
          height: size,
          fill: "none",
          stroke: "currentColor",
          strokeWidth: "1.3",
          strokeLinecap: "round",
          strokeLinejoin: "round",
          "aria-hidden": "true",
        },
        h("path", { d: "M2.2 2.9h4.1c.9 0 1.7.7 1.7 1.6v8.6c0-.7-.6-1.3-1.3-1.3H2.2z" }),
        h("path", { d: "M13.8 2.9H9.7c-.9 0-1.7.7-1.7 1.6v8.6c0-.7.6-1.3 1.3-1.3h4.5z" }),
        h("path", { d: "M4.6 5.3h1.8M4.6 7.6h1.8M9.6 5.6h1.8" }),
      );
    }

    function ToolbarButton(props) {
      var disabled = !!props.disabled;
      return h(
        "button",
        {
          type: "button",
          onClick: props.onClick,
          disabled: disabled,
          title: props.title || "",
          "data-dsh-story-action": props.action || "",
          style: {
            padding: "5px 12px",
            borderRadius: 7,
            border: "1px solid " + COLORS.border,
            background: props.primary && !disabled ? "color-mix(in srgb, currentColor 12%, transparent)" : "transparent",
            color: "inherit",
            font: "inherit",
            fontSize: 13,
            cursor: disabled ? "default" : "pointer",
            opacity: disabled ? 0.45 : 1,
            whiteSpace: "nowrap",
          },
        },
        props.children,
      );
    }

    /**
     * 主面板页：工具条 + 同源 iframe。
     * @param {object} props 框架 main 座位 share + 本插件注入的面（askDsh）。
     */
    function StoryPanelPage(props) {
      var askDsh = props.askDsh;
      var iframeRef = react.useRef(null);
      var pendingRef = react.useRef(null);
      if (!pendingRef.current) pendingRef.current = new Map();
      var seqRef = react.useRef(0);
      // 编辑器填充的令牌：同一时刻只允许一个填充任务，新的动作会取消上一个。
      var fillSeqRef = react.useRef(0);

      var [loaded, setLoaded] = react.useState(false);
      var [connected, setConnected] = react.useState(false);
      var [appPath, setAppPath] = react.useState("");
      var [hasContext, setHasContext] = react.useState(false);
      var [extra, setExtra] = react.useState("");
      var [notice, setNotice] = react.useState("");
      var [busy, setBusy] = react.useState(false);
      var [sent, setSent] = react.useState(false);
      var [copied, setCopied] = react.useState(false);

      /** 同源 iframe 地址：宿主把 /story 反代到应用，见 lib/index.js。 */
      var homeHref = react.useMemo(function () {
        try {
          return new URL("story/", document.baseURI).href;
        } catch (error) {
          return "/story/";
        }
      }, []);

      // 首次挂载：有「最后一次有效路由」就恢复到那里，否则回应用首页。
      // 只在挂载时算一次：后续 iframe 的 src 变更走 reload()，不跟记忆互相打架。
      var initialSrcRef = react.useRef(null);
      if (initialSrcRef.current === null) {
        initialSrcRef.current = frameHrefForAppPath(rememberedAppPath) || homeHref;
      }
      var fromMemoryRef = react.useRef(initialSrcRef.current !== homeHref);
      // 是否仍在从记忆恢复：新文档 onLoad 之前应用上报的是导航中间态，不拿来改记忆。
      var restoringRef = react.useRef(fromMemoryRef.current);
      var [restored, setRestored] = react.useState(fromMemoryRef.current);

      /** 往 iframe 发一条消息（同源校验由浏览器与接收端保证）。 */
      function postToFrame(payload) {
        var frame = iframeRef.current;
        var win = frame && frame.contentWindow;
        if (!win) return false;
        try {
          win.postMessage(payload, window.location.origin);
          return true;
        } catch (error) {
          return false;
        }
      }

      /**
       * 向 iframe 要当前故事上下文；返回 Promise<{context, error}>。
       * 用 id 关联请求与响应，超时/断连都不会把界面卡死。
       */
      function requestContext(kind, target) {
        if (!postToFrame({ type: MSG.ping })) {
          return Promise.resolve({ context: null, error: "内嵌页面还没准备好" });
        }
        seqRef.current += 1;
        var id = "story-" + Date.now().toString(36) + "-" + seqRef.current;
        return new Promise(function (resolve) {
          var timer = window.setTimeout(function () {
            pendingRef.current.delete(id);
            resolve({ context: null, error: "应用没有响应（8 秒超时）" });
          }, CONTEXT_TIMEOUT_MS);
          pendingRef.current.set(id, { resolve: resolve, timer: timer });
          var payload = { type: MSG.contextRequest, id: id, kind: kind };
          if (target) payload.target = target;
          if (!postToFrame(payload)) {
            window.clearTimeout(timer);
            pendingRef.current.delete(id);
            resolve({ context: null, error: "内嵌页面还没准备好" });
          }
        });
      }

      /** 应用侧主动上报的连接状态 / ping 的回应，都用来更新「已连接」提示。 */
      function markConnected(path) {
        setConnected(true);
        if (typeof path === "string" && path) setAppPath(path);
      }

      /**
       * 记住「最后一次有效路由」：应用报告了上下文（hasContext=true）或停在
       * /worlds/ 路由下才记；`/` 首页没有恢复价值；恢复过程中不覆盖记忆。
       */
      function rememberRoute(path, hasContext) {
        if (restoringRef.current) return;
        if (!isUsableAppPath(path)) return;
        if (!hasContext && !/^\/worlds(\/|$)/.test(path)) return;
        if (path === "/") return;
        if (path === rememberedAppPath) return;
        storeRememberedPath(path);
      }

      react.useEffect(function () {
        function onMessage(event) {
          // 同源 + event.source 双重校验：只认自己的 iframe。
          if (event.origin !== window.location.origin) return;
          var data = event.data;
          if (!data || typeof data.type !== "string") return;
          var frame = iframeRef.current;
          if (frame && frame.contentWindow && event.source && event.source !== frame.contentWindow) return;

          if (data.type === MSG.pong) {
            markConnected(data.appPath);
            // pong 不带 hasContext：只有 /worlds/ 路由才敢当作可恢复位置。
            rememberRoute(data.appPath, false);
            return;
          }
          if (data.type === MSG.state) {
            markConnected(data.appPath);
            setHasContext(!!data.hasContext);
            rememberRoute(data.appPath, !!data.hasContext);
            return;
          }
          if (data.type === MSG.context) {
            var entry = pendingRef.current.get(data.id);
            if (!entry) return;
            pendingRef.current.delete(data.id);
            window.clearTimeout(entry.timer);
            entry.resolve({
              context: data.ok && data.context ? data.context : null,
              error: data.ok ? "" : String(data.error || "应用读取故事内容失败"),
            });
          }
        }
        window.addEventListener("message", onMessage);
        return function () {
          window.removeEventListener("message", onMessage);
        };
      }, []);

      // 宿主切浅色/深色时把新主题推给内嵌页：只 postMessage，不重载，正文不丢。
      react.useEffect(function () {
        var current = hostTheme();
        var observer = null;
        try {
          observer = new MutationObserver(function () {
            var next = hostTheme();
            if (next === current) return;
            current = next;
            postToFrame({ type: MSG.theme, theme: next });
          });
          observer.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ["style", "class", "data-theme", "data-ds-theme-source"],
          });
        } catch (error) {}
        return function () {
          if (observer) observer.disconnect();
        };
      }, []);

      // 卸载时把还挂着的 context 请求都收掉，避免定时器泄漏。
      react.useEffect(function () {
        return function () {
          pendingRef.current.forEach(function (entry) {
            try {
              window.clearTimeout(entry.timer);
            } catch (error) {}
          });
          pendingRef.current.clear();
        };
      }, []);

      function onFrameLoad() {
        // 新文档已就位：从这里开始应用上报的路由才是「现在的真实位置」，可以记了。
        restoringRef.current = false;
        setLoaded(true);
        setConnected(false);
        setNotice("");
        postToFrame({ type: MSG.theme, theme: hostTheme() });
        postToFrame({ type: MSG.ping });
        // 应用侧可能晚一点才挂好监听：补几次 ping。只影响「已连接」提示，
        // 真正的上下文请求走 requestContext，超时自己会兜底。
        [400, 1200, 3000].forEach(function (delay) {
          window.setTimeout(function () {
            postToFrame({ type: MSG.ping });
          }, delay);
        });
      }

      function reload() {
        // 刷新是「恢复失败」的兜底：清掉位置记忆，回应用首页重新加载。
        forgetRememberedPath();
        restoringRef.current = false;
        var frame = iframeRef.current;
        if (frame) frame.src = homeHref;
        setLoaded(false);
        setConnected(false);
        setAppPath("");
        setHasContext(false);
        setRestored(false);
        setSent(false);
        setCopied(false);
        setNotice("");
      }

      /**
       * 把当前故事交给 DSH Agent：先要上下文，再组 prompt，然后新开会话。
       * 会话草稿在宿主的 Lexical 编辑器里，插件拿不到写入 API；这里先复制到
       * 剪贴板兜底，新会话出现后再用浏览器原生插入命令「粘」进编辑器。
       */
      function startAsk(kind) {
        if (busy || !loaded) return;
        // 取消上一个还在等编辑器的填充任务，避免两个动作抢着往同一个输入框里写。
        fillSeqRef.current += 1;
        setBusy(true);
        setSent(false);
        setCopied(false);
        setNotice(kind === "roleplay" ? "正在读取当前角色与情境…" : "正在读取当前故事…");
        requestContext(kind, null).then(function (result) {
          if (!result.context) {
            setBusy(false);
            setNotice("读取故事内容失败：" + (result.error || "未知原因") + "。请确认正文页已打开后点「刷新」重试。");
            return;
          }
          var context = result.context;
          var target = null;
          if (kind === "roleplay") {
            target = pickRoleplayTarget(context, extra);
            if (!target) {
              setBusy(false);
              setNotice("当前故事没有可扮演的角色，先打开一章正文再试。");
              return;
            }
          }
          var prompt =
            kind === "roleplay"
              ? buildRoleplayPrompt(context, target, stripRoleToken(extra))
              : buildContinuePrompt(context, extra);

          // 记下当前已有的输入框：新会话的编辑器是重新挂载的，写入时避开这些旧节点。
          var before = composerSnapshot();
          copyText(prompt);
          setNotice("正在打开 DSH 会话…");
          Promise.resolve(askDsh(prompt)).then(function (ok) {
            setBusy(false);
            if (!ok) {
              setNotice("打开 DSH 会话失败，请重试。");
              return;
            }
            setSent(true);
            fillComposer(prompt, before).then(function (filled) {
              // 没写进编辑器时把提示切到「用剪贴板粘贴」，不要谎报已填好。
              setCopied(!filled);
              setNotice(
                filled
                  ? "已新开会话并预填内容，检查后按发送即可。"
                  : "已新开会话；内容已复制到剪贴板，粘贴到输入框发送即可。",
              );
            });
          });
        });
      }

      /**
       * 会话草稿在宿主的 Lexical 编辑器里，插件拿不到写入 API，
       * 于是等新会话的输入框出现后，用浏览器原生的插入命令把文本「粘」进去
       * （等价于用户粘贴，可撤销）；失败则退化为只给剪贴板。
       */
      var COMPOSER_SELECTOR = [
        '[data-lexical-editor="true"]',
        '[contenteditable="true"][role="textbox"]',
        '[contenteditable="true"]',
        'textarea',
      ].join(',');

      /** 当前页面上所有候选输入框（用来识别「切会话之后才挂载的新编辑器」）。 */
      function composerSnapshot() {
        return Array.prototype.slice.call(document.querySelectorAll(COMPOSER_SELECTOR));
      }

      /**
       * 找到会话输入框：优先 Lexical 编辑器本体，退 textarea。
       *
       * `avoid` 里是切换会话之前就存在的编辑器：新会话的编辑器是重新挂载的，
       * 如果命中旧节点，说明面板还没切过去 —— 这时千万别往里写，
       * 否则故事 prompt 会落到上一个会话的草稿里。
       */
      function findComposer(avoid) {
        var nodes = composerSnapshot();
        for (var i = 0; i < nodes.length; i += 1) {
          if (avoid && avoid.indexOf(nodes[i]) !== -1) continue;
          return nodes[i];
        }
        return null;
      }

      /** 把 text 插进当前编辑器光标处；返回是否真的写进去了。 */
      function insertIntoEditor(editor, text) {
        try {
          if (editor.tagName === 'TEXTAREA') {
            editor.focus();
            editor.value = text;
            editor.dispatchEvent(new Event('input', { bubbles: true }));
            return true;
          }
          editor.focus();
          var selection = window.getSelection();
          var range = document.createRange();
          range.selectNodeContents(editor);
          range.collapse(false);
          selection.removeAllRanges();
          selection.addRange(range);
          var ok = document.execCommand('insertText', false, text);
          if (!ok) {
            document.execCommand('insertHTML', false, text.split('\n').map(function (line) {
              return line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            }).join('<br>'));
          }
          return editor.innerText.replace(/\s+/g, ' ').indexOf(text.slice(0, 12)) !== -1;
        } catch (error) {
          return false;
        }
      }

      /**
       * 等新会话的输入框出现并写入 prompt。
       *
       * 会议话页有时要好几秒才挂载（工作区多、会话列表在加载），所以轮询 15 秒；
       * 写进去后还读回来校验一次，React 把内容重置掉就再写一遍。
       */
      function fillComposer(text, avoid) {
        fillSeqRef.current += 1;
        var token = fillSeqRef.current;
        var deadline = Date.now() + 15000;
        var needle = text.slice(0, 12);
        return new Promise(function (resolve) {
          var tries = 0;
          function attempt() {
            if (token !== fillSeqRef.current) return resolve(false);
            var editor = findComposer(avoid);
            if (!editor) {
              if (Date.now() > deadline) return resolve(false);
              return setTimeout(attempt, 250);
            }
            var current =
              editor.tagName === 'TEXTAREA'
                ? String(editor.value || '')
                : String(editor.innerText || '');
            if (current.replace(/\s+/g, ' ').indexOf(needle) !== -1) return resolve(true);
            insertIntoEditor(editor, text);
            tries += 1;
            if (tries > 6) return resolve(false);
            setTimeout(attempt, 500);
          }
          attempt();
        });
      }

      /** 把文本写入剪贴板（失败时不阻塞主流程）。 */
      function copyText(text) {
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return false; });
          }
        } catch (error) {}
        return Promise.resolve(false);
      }

      // 状态提示：notice（动作反馈）优先；否则按加载/连接情况给一句稳定的提示。
      var statusText = notice;
      if (!statusText) {
        if (!loaded) statusText = "正在加载故事应用…";
        else if (!connected) statusText = "等待应用就绪…（若一直没有反应，点「刷新」）";
        else if (!hasContext) statusText = "已连接；打开一章正文后即可让 DSH 续写";
        else if (appPath) statusText = "当前：" + appPath;
        else statusText = "已连接故事应用";
      }
      // 给自动化验收一个稳定的状态锚点：idle / loading / waiting / ready / busy / sent / error。
      var hintState = "idle";
      if (notice && /失败|没能|没有|超时|不可用/.test(notice)) hintState = "error";
      else if (busy) hintState = "busy";
      else if (sent) hintState = "sent";
      else if (!loaded) hintState = "loading";
      else if (!connected) hintState = "waiting";
      else hintState = "ready";

      return h(
        "div",
        {
          "data-dsh-story-view": "",
          "data-dsh-plugin": "story",
          "data-dsh-story-connected": connected ? "1" : "0",
          "data-dsh-story-restored": restored ? "1" : "0",
          "data-dsh-story-hint": hintState,
          style: {
            display: "flex",
            flexDirection: "column",
            height: "100%",
            minHeight: 0,
            width: "100%",
          },
        },
        h(
          "div",
          {
            style: {
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "8px 14px",
              borderBottom: "1px solid " + COLORS.border,
              flexShrink: 0,
              flexWrap: "wrap",
            },
          },
          h("span", { style: { fontSize: 14, fontWeight: 600 } }, "故事之间"),
          h(
            "span",
            {
              "data-dsh-story-status": "",
              style: { fontSize: 12, color: COLORS.muted, maxWidth: "38vw", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
            },
            statusText,
          ),
          h("span", { style: { flex: 1 } }),
          h("input", {
            type: "text",
            "data-dsh-story-extra": "",
            value: extra,
            placeholder: "续写要求 / @角色名（可选）",
            onChange: function (event) {
              setExtra(event.target.value);
            },
            onKeyDown: function (event) {
              if (event.key === "Enter") startAsk("continue");
            },
            style: {
              width: 240,
              maxWidth: "42vw",
              padding: "5px 10px",
              borderRadius: 7,
              border: "1px solid " + COLORS.border,
              background: COLORS.chip,
              color: "inherit",
              font: "inherit",
              fontSize: 13,
            },
          }),
          h(
            ToolbarButton,
            {
              primary: true,
              action: "continue",
              disabled: !loaded || busy,
              onClick: function () {
                startAsk("continue");
              },
              title: "新开一个 DSH 会话，把当前故事交给 Agent 续写",
            },
            sent ? "已交给 DSH ✓" : "让 DSH 续写",
          ),
          h(
            ToolbarButton,
            {
              action: "roleplay",
              disabled: !loaded || busy,
              onClick: function () {
                startAsk("roleplay");
              },
              title: "新开一个 DSH 会话，由 Agent 以当前角色第一人称即兴演绎",
            },
            "角色扮演",
          ),
          h(
            ToolbarButton,
            {
              action: "reload",
              onClick: reload,
              title: "回到故事应用首页并重新加载（恢复失败时用它兜底）",
            },
            "刷新",
          ),
          h(
            "a",
            {
              "data-dsh-story-action": "open",
              href: homeHref,
              target: "_blank",
              rel: "noreferrer",
              style: { fontSize: 12, color: COLORS.muted, textDecoration: "none" },
            },
            "新窗口打开",
          ),
        ),
        h("iframe", {
          ref: iframeRef,
          // src 只在挂载时取一次（记忆地址或首页）；之后变更走 reload() 直接改 DOM。
          src: initialSrcRef.current,
          title: "故事之间",
          "data-dsh-story-frame": "",
          onLoad: onFrameLoad,
          style: {
            flex: 1,
            minHeight: 0,
            width: "100%",
            border: 0,
            background: "transparent",
          },
        }),
      );
    }

    /** 由宿主注入的服务签名：客户端根 context。 */
    var inject = ["slots", "layout"];

    function apply(ctx) {
      var disposers = [];

      /** 取一个可用的 Workspace id：已有工作区就交给宿主按「当前/最近」继承。 */
      function listWorkspaces() {
        try {
          var service = ctx.get ? ctx.get("workspaces", false) : null;
          var source = service && service.list;
          if (!source || typeof source.getSnapshot !== "function") return null;
          var snapshot = source.getSnapshot();
          return snapshot && snapshot.items ? snapshot.items : null;
        } catch (error) {
          return null;
        }
      }

      /**
       * 把故事交给 Agent：新开会话并预填 prompt，然后切回会话面板。
       * 前沿：已有 Workspace 时 startSession(undefined) 会继承「当前/最近」工作区；
       * 全新 DSH_HOME（一个 Workspace 都没有）会以「Choose a workspace first」失败，
       * 这时先让 host 初始化默认工作区再重试。
       */
      async function askDsh(prompt, workspaceId) {
        try {
          var navigation = ctx.get ? ctx.get("uiWorkspace", false) : null;
          var layout = ctx.get ? ctx.get("layout", false) : null;
          if (!navigation || typeof navigation.startSession !== "function") {
            console.warn("[dsh-story-between] uiWorkspace 不可用，无法新建会话");
            return false;
          }
          // 注意：宿主的 startSession 是**同步 void**（navigation.d.ts），await 是空操作。
          // 别指望它「等会话建好」；会话是否真的打开要靠下面切面板 + 新编辑器出现来确认。
          navigation.startSession(workspaceId, { prompt: prompt, whenDraftExists: "append" });
          if (layout && typeof layout.selectPanel === "function") layout.selectPanel(null);
          return true;
        } catch (error) {
          console.warn("[dsh-story-between] askDsh 失败:", error);
          return false;
        }
      }

      /** 没有 Workspace 时先建默认的，再问一次。 */
      async function ensureWorkspaceThenAsk(prompt) {
        var items = listWorkspaces();
        if (items && items.length > 0) return askDsh(prompt);
        try {
          var service = ctx.get ? ctx.get("workspaces", false) : null;
          if (service && typeof service.initializeDefault === "function") {
            var view = await service.initializeDefault();
            if (view && view.id) return askDsh(prompt, view.id);
          }
        } catch (error) {
          console.warn("[dsh-story-between] 初始化默认 Workspace 失败:", error);
        }
        return askDsh(prompt);
      }

      try {
        var slots = ctx.slots;
        disposers.push(
          slots.inject("sidebar.panellist", function () {
            return slots.register(
              { name: "sidebar.panellist", id: PANEL_ID, order: PANEL_ORDER, label: function () { return "故事之间"; } },
              StoryPanelIcon,
            );
          }),
        );
        disposers.push(
          slots.inject("main", function () {
            return slots.register(
              { name: "main", key: PANEL_ID, inject: function () { return { askDsh: ensureWorkspaceThenAsk }; } },
              StoryPanelPage,
            );
          }),
        );
      } catch (error) {
        console.warn("[dsh-story-between] panel registration failed:", error);
      }

      ctx.effect(function () {
        return function () {
          for (var i = 0; i < disposers.length; i += 1) {
            try {
              disposers[i]();
            } catch {}
          }
          disposers.length = 0;
        };
      }, "dsh-story-between: ui mounts");
    }

    exports.apply = apply;
    exports.inject = inject;
    return module.exports;
  },
});
