/**
 * Shared auth helpers for Shanghai EIA web UI.
 * When auth is disabled server-side, these helpers behave as plain fetch.
 */
(function (global) {
  const TOKEN_KEY = "sh_eia_token";
  const USER_KEY = "sh_eia_user";

  function getToken() {
    return localStorage.getItem(TOKEN_KEY) || "";
  }

  function getStoredUser() {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch (_) {
      return null;
    }
  }

  function setAuth(token, user) {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
    else localStorage.removeItem(USER_KEY);

    const secure = location.protocol === "https:" ? "; Secure" : "";
    if (token) {
      document.cookie = `${TOKEN_KEY}=${encodeURIComponent(token)}; Path=/; SameSite=Lax${secure}`;
    } else {
      document.cookie = `${TOKEN_KEY}=; Path=/; Max-Age=0; SameSite=Lax${secure}`;
    }
  }

  function clearAuth() {
    setAuth("", null);
  }

  function loginUrl() {
    const next = encodeURIComponent(location.pathname + location.search);
    return `/login?next=${next}`;
  }

  function redirectToLogin() {
    if (location.pathname === "/login") return;
    clearAuth();
    location.href = loginUrl();
  }

  async function fetchAuthConfig() {
    try {
      const res = await fetch("/api/auth/config", { cache: "no-store" });
      if (!res.ok) return { auth_enabled: false };
      return await res.json();
    } catch (_) {
      return { auth_enabled: false };
    }
  }

  async function authFetch(url, options = {}) {
    const opts = { ...options };
    const headers = new Headers(opts.headers || {});
    const token = getToken();
    if (token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    opts.headers = headers;
    const res = await fetch(url, opts);
    if (res.status === 401) {
      const cfg = await fetchAuthConfig();
      if (cfg.auth_enabled) {
        redirectToLogin();
        throw new Error("未登录");
      }
    }
    return res;
  }

  function apiErrorMessage(data, fallback = "请求失败") {
    if (!data || data.detail == null) return fallback;
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail.map((item) => item.msg || item).join("；");
    }
    return fallback;
  }

  function filenameFromDisposition(disp) {
    if (!disp) return "";
    const star = disp.match(/filename\*=UTF-8''([^;]+)/i);
    if (star) {
      try {
        return decodeURIComponent(star[1]);
      } catch (_) {
        /* ignore */
      }
    }
    const plain = disp.match(/filename="?([^";]+)"?/i);
    return plain ? plain[1] : "";
  }

  function saveBlobDownload(blob, filename) {
    const a = document.createElement("a");
    const url = URL.createObjectURL(blob);
    a.href = url;
    a.download = filename;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  async function downloadWithAuth(url, fallbackName) {
    const res = await authFetch(url);
    if (!res.ok) {
      let message = "下载失败";
      try {
        message = apiErrorMessage(await res.json(), message);
      } catch (_) {}
      throw new Error(message);
    }
    const blob = await res.blob();
    const filename = filenameFromDisposition(res.headers.get("Content-Disposition")) || fallbackName;
    saveBlobDownload(blob, filename);
    return filename;
  }

  function ensureChangePasswordModal() {
    let mask = document.getElementById("changePasswordMask");
    if (mask) return mask;
    mask = document.createElement("div");
    mask.id = "changePasswordMask";
    mask.setAttribute("role", "dialog");
    mask.innerHTML = `
      <style>
        #changePasswordMask {
          display: none; position: fixed; inset: 0; z-index: 2000;
          background: rgba(15,23,42,.45); align-items: center; justify-content: center; padding: 20px;
        }
        #changePasswordMask.show { display: flex; }
        #changePasswordMask .cp-card {
          width: min(400px, 100%); background: #fff; border-radius: 14px; padding: 22px 22px 18px;
          box-shadow: 0 18px 40px rgba(0,0,0,.2); color: #1f2937;
          font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
        }
        #changePasswordMask h3 { margin: 0 0 14px; font-size: 18px; color: #2563eb; }
        #changePasswordMask label { display: block; font-size: 13px; font-weight: 600; color: #475569; margin: 0 0 6px; }
        #changePasswordMask .cp-field { margin-bottom: 12px; }
        #changePasswordMask .cp-hint { margin: 6px 0 0; font-size: 12px; color: #64748b; line-height: 1.4; }
        #changePasswordMask input {
          width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid #e5e7eb;
          border-radius: 8px; font-size: 15px;
        }
        #changePasswordMask .cp-actions { display: flex; gap: 8px; margin-top: 8px; }
        #changePasswordMask button {
          border: 0; border-radius: 8px; padding: 10px 14px; cursor: pointer; font-size: 14px;
        }
        #changePasswordMask .cp-submit { flex: 1; background: #2563eb; color: #fff; }
        #changePasswordMask .cp-cancel { background: #eef2ff; color: #1e3a8a; }
        #changePasswordMask .cp-msg { min-height: 1.2em; margin-top: 10px; font-size: 13px; color: #1e40af; }
        #changePasswordMask .cp-msg.error { color: #dc2626; }
      </style>
      <div class="cp-card">
        <h3>修改密码</h3>
        <form id="changePasswordForm">
          <div class="cp-field">
            <label for="cpCurrent">当前密码</label>
            <input id="cpCurrent" type="password" required autocomplete="current-password">
          </div>
          <div class="cp-field">
            <label for="cpNew">新密码</label>
            <input id="cpNew" type="password" required minlength="8" maxlength="128" autocomplete="new-password" title="密码至少 8 位">
            <p class="cp-hint">密码长度至少 8 位，最长 128 位</p>
          </div>
          <div class="cp-field">
            <label for="cpNew2">确认新密码</label>
            <input id="cpNew2" type="password" required minlength="8" maxlength="128" autocomplete="new-password" title="请再次输入密码，至少 8 位">
            <p class="cp-hint">请再次输入密码（至少 8 位）</p>
          </div>
          <div class="cp-actions">
            <button type="button" class="cp-cancel" id="cpCancel">取消</button>
            <button type="submit" class="cp-submit">确认修改</button>
          </div>
          <div class="cp-msg" id="cpMsg"></div>
        </form>
      </div>
    `;
    document.body.appendChild(mask);

    const setMsg = (text, isError) => {
      const el = document.getElementById("cpMsg");
      el.textContent = text || "";
      el.className = "cp-msg" + (isError ? " error" : "");
    };

    const close = () => {
      mask.classList.remove("show");
      setMsg("");
      document.getElementById("changePasswordForm").reset();
    };

    document.getElementById("cpCancel").addEventListener("click", close);
    mask.addEventListener("click", (e) => {
      if (e.target === mask) close();
    });
    document.getElementById("changePasswordForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const current_password = document.getElementById("cpCurrent").value;
      const new_password = document.getElementById("cpNew").value;
      const new2 = document.getElementById("cpNew2").value;
      if (new_password !== new2) {
        setMsg("两次输入的新密码不一致", true);
        return;
      }
      if (new_password.length < 8) {
        setMsg("新密码长度至少 8 位", true);
        return;
      }
      setMsg("正在提交…");
      try {
        const res = await authFetch("/api/auth/change-password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ current_password, new_password }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          setMsg(apiErrorMessage(data, "修改失败"), true);
          return;
        }
        setMsg(data.message || "密码已修改");
        clearAuth();
        setTimeout(() => {
          location.href = "/login";
        }, 800);
      } catch (err) {
        setMsg(err.message || "修改失败", true);
      }
    });
    return mask;
  }

  function openChangePasswordModal() {
    const mask = ensureChangePasswordModal();
    mask.classList.add("show");
    document.getElementById("cpCurrent")?.focus();
  }

  global.ShEiaAuth = {
    TOKEN_KEY,
    getToken,
    getStoredUser,
    setAuth,
    clearAuth,
    loginUrl,
    redirectToLogin,
    fetchAuthConfig,
    authFetch,
    apiErrorMessage,
    filenameFromDisposition,
    saveBlobDownload,
    downloadWithAuth,
    openChangePasswordModal,
  };
})(window);
