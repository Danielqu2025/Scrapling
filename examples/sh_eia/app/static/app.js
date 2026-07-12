/**
 * App shell: auth gate for index, header user menu.
 */
(function () {
  const auth = window.ShEiaAuth;
  if (!auth) return;

  function ensureHeaderMount() {
    let mount = document.getElementById("userBar");
    if (mount) return mount;
    const header = document.querySelector("header");
    if (!header) return null;
    mount = document.createElement("div");
    mount.id = "userBar";
    mount.className = "user-bar";
    header.appendChild(mount);
    return mount;
  }

  function renderUserBar(user, authEnabled) {
    const mount = ensureHeaderMount();
    if (!mount) return;
    mount.hidden = false;
    const homeLink =
      location.pathname === "/settings" || location.pathname.startsWith("/admin")
        ? `<a class="user-bar-link" href="/">返回检索</a>`
        : "";
    const settingsLink =
      location.pathname === "/settings"
        ? ""
        : `<a class="user-bar-link" href="/settings">设置</a>`;

    if (!authEnabled) {
      mount.innerHTML = `${homeLink}${settingsLink}`;
      return;
    }

    const name = user?.display_name || user?.username || "用户";
    const adminLink =
      user?.role === "admin"
        ? `<a class="user-bar-link" href="/admin">用户管理</a>`
        : "";
    mount.innerHTML = `
      <span class="user-bar-name">${name}</span>
      ${homeLink}
      ${settingsLink}
      ${adminLink}
      <button type="button" id="changePasswordBtn" class="user-bar-btn">修改密码</button>
      <button type="button" id="logoutBtn" class="user-bar-btn">退出</button>
    `;
    document.getElementById("changePasswordBtn")?.addEventListener("click", () => {
      auth.openChangePasswordModal();
    });
    document.getElementById("logoutBtn")?.addEventListener("click", () => {
      auth.clearAuth();
      location.href = "/login";
    });
  }

  async function initIndexAuth() {
    const cfg = await auth.fetchAuthConfig();
    if (!cfg.auth_enabled) {
      renderUserBar(null, false);
      document.dispatchEvent(new CustomEvent("sh-eia-auth-ready", { detail: { authEnabled: false } }));
      return true;
    }
    if (!auth.getToken()) {
      auth.redirectToLogin();
      return false;
    }
    try {
      const res = await auth.authFetch("/api/auth/me");
      if (!res.ok) {
        auth.redirectToLogin();
        return false;
      }
      const data = await res.json();
      auth.setAuth(auth.getToken(), data.user);
      renderUserBar(data.user, true);
      document.dispatchEvent(
        new CustomEvent("sh-eia-auth-ready", { detail: { authEnabled: true, user: data.user } })
      );
      return true;
    } catch (_) {
      auth.redirectToLogin();
      return false;
    }
  }

  window.ShEiaApp = { initIndexAuth, renderUserBar };

  function shouldInitAuth() {
    const path = location.pathname;
    return path === "/" || path === "/index.html" || path === "/settings";
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      if (shouldInitAuth()) initIndexAuth();
    });
  } else if (shouldInitAuth()) {
    initIndexAuth();
  }
})();
