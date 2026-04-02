const authScreen = document.getElementById("auth-screen");
const menuScreen = document.getElementById("menu-screen");
const connectBtn = document.getElementById("connect-btn");
const devLoginBtn = document.getElementById("dev-login-btn");
const disconnectBtn = document.getElementById("disconnect-btn");
const authStatus = document.getElementById("auth-status");
const walletShort = document.getElementById("wallet-short");
const gameStatus = document.getElementById("game-status");
const pointsLine = document.getElementById("points-line");
const rateLine = document.getElementById("rate-line");
const tokenLine = document.getElementById("token-line");
const statsLine = document.getElementById("stats-line");
const actionButtons = document.querySelectorAll("[data-action]");
const accessoryList = document.getElementById("accessory-list");
const openCaseBtn = document.getElementById("open-case-btn");
const casePreview = document.getElementById("case-preview");
const caseResult = document.getElementById("case-result");

const tg = window.Telegram?.WebApp;
if (tg) {
  tg.expand();
  tg.ready();
}

let currentAccessories = [];
let caseRewards = [];
let caseCostXp = 20000;
let balanceRefreshTimer = null;
let isLoadingState = false;

async function parseApiResponse(resp) {
  const raw = await resp.text();
  let data = null;
  try {
    data = JSON.parse(raw);
  } catch {
    data = null;
  }

  if (!data) {
    if (raw.includes("Tunnel Password") || raw.includes("loca.lt/mytunnelpassword")) {
      throw new Error("Туннель localtunnel просит пароль. Открой https://loca.lt/mytunnelpassword.");
    }
    if (raw.includes("503 - Tunnel Unavailable")) {
      throw new Error("Туннель недоступен (503). Перезапусти localtunnel и обнови WEBAPP_URL.");
    }
    throw new Error("Сервер вернул не JSON-ответ. Проверь туннель и miniapp/app.py.");
  }

  return data;
}

function getTelegramId() {
  const realId = tg?.initDataUnsafe?.user?.id?.toString();
  if (realId) {
    return realId;
  }

  // Fallback for clients where Telegram user id is not exposed in WebView.
  const key = "fallbackTelegramId";
  const saved = localStorage.getItem(key);
  if (saved) {
    return saved;
  }
  const generated = `guest_${Date.now()}`;
  localStorage.setItem(key, generated);
  return generated;
}

function shortAddress(address) {
  return `${address.slice(0, 4)}...${address.slice(-4)}`;
}

function showMenu(address) {
  authScreen.classList.remove("active");
  menuScreen.classList.add("active");
  walletShort.textContent = `Кошелек: ${shortAddress(address)}`;
  loadGameState();
  startBalanceAutoRefresh();
}

function showAuth() {
  menuScreen.classList.remove("active");
  authScreen.classList.add("active");
  walletShort.textContent = "";
  gameStatus.textContent = "";
  stopBalanceAutoRefresh();
}

async function connectPhantom() {
  const telegramId = getTelegramId();
  if (!window.solana || !window.solana.isPhantom) {
    authStatus.textContent = "Phantom не найден. Нажми 'Войти без Phantom (тест)'.";
    return;
  }

  try {
    if (telegramId.startsWith("guest_")) {
      authStatus.textContent = "Режим совместимости Telegram: использую fallback ID.";
    }
    authStatus.textContent = "Подключение...";
    const resp = await window.solana.connect();
    const address = resp.publicKey.toString();
    const challengeResp = await fetch("/api/auth/challenge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wallet: address, telegram_id: telegramId }),
    });
    const challengeData = await parseApiResponse(challengeResp);
    if (!challengeResp.ok || !challengeData.ok) {
      throw new Error(challengeData.error || "Ошибка challenge");
    }

    const messageBytes = new TextEncoder().encode(challengeData.challenge);
    const signed = await window.solana.signMessage(messageBytes, "utf8");
    const signatureB64 = bytesToBase64(signed.signature);

    const verifyResp = await fetch("/api/auth/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        wallet: address,
        telegram_id: telegramId,
        challenge: challengeData.challenge,
        signature: signatureB64,
      }),
    });
    const verifyData = await parseApiResponse(verifyResp);
    if (!verifyResp.ok || !verifyData.ok) {
      throw new Error(verifyData.error || "Ошибка verify");
    }

    localStorage.setItem("walletAddress", verifyData.wallet);
    authStatus.textContent = "Успешная авторизация";
    showMenu(verifyData.wallet);
  } catch (error) {
    authStatus.textContent = `Ошибка: ${error.message || "подключение отменено"}`;
  }
}

async function loginWithoutPhantom() {
  const telegramId = getTelegramId();
  authStatus.textContent = "Вход в тестовом режиме...";
  try {
    const resp = await fetch("/api/auth/dev-login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ telegram_id: telegramId }),
    });
    const data = await parseApiResponse(resp);
    if (!resp.ok || !data.ok) {
      throw new Error(data.error || "dev login failed");
    }
    localStorage.setItem("walletAddress", data.wallet);
    authStatus.textContent = "Вход выполнен (тестовый режим)";
    showMenu(data.wallet);
  } catch (error) {
    authStatus.textContent = `Ошибка: ${error.message || "не удалось войти"}`;
  }
}

async function disconnectPhantom() {
  const telegramId = getTelegramId();
  try {
    if (window.solana?.isConnected) {
      await window.solana.disconnect();
    }
    if (telegramId) {
      await fetch("/api/auth/unlink", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ telegram_id: telegramId }),
      });
    }
  } finally {
    localStorage.removeItem("walletAddress");
    showAuth();
    authStatus.textContent = "Кошелек отключен";
  }
}

function bytesToBase64(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

async function restoreSessionFromServer() {
  const telegramId = getTelegramId();
  if (!telegramId) {
    return;
  }

  try {
    const resp = await fetch(`/api/auth/status?telegram_id=${encodeURIComponent(telegramId)}`);
    const data = await parseApiResponse(resp);
    if (resp.ok && data.ok && data.authorized && data.wallet) {
      localStorage.setItem("walletAddress", data.wallet);
      showMenu(data.wallet);
    }
  } catch {
    // Silent fail: user can still connect manually.
  }
}

function renderState(state) {
  if (!state) {
    return;
  }
  pointsLine.textContent = `XP за уход: ${state.xp}`;
  rateLine.textContent = `Пассивный доход: ${state.passive_xp_per_hour} XP/час`;
  tokenLine.textContent = `MGPT: ${state.mgpt_balance}`;
  statsLine.textContent =
    `Статы: mood ${state.stats.mood} | hunger ${state.stats.hunger} | hygiene ${state.stats.hygiene} | energy ${state.stats.energy}`;

  if (state.boost_until > Math.floor(Date.now() / 1000)) {
    gameStatus.textContent = `Активен буст x${state.boost_multiplier} XP/час`;
  }
}

function renderAccessories(items) {
  currentAccessories = items || [];
  accessoryList.innerHTML = "";
  currentAccessories.forEach((item) => {
    const row = document.createElement("div");
    row.className = "accessory-item";

    const meta = document.createElement("div");
    meta.className = "accessory-meta";
    meta.textContent = `${item.name} | +${item.bonus_xp_per_hour} XP/час | ${item.cost_xp} XP`;

    const btn = document.createElement("button");
    btn.className = "buy-btn";
    btn.textContent = item.owned ? "Куплено" : "Купить";
    btn.disabled = item.owned;
    btn.addEventListener("click", () => buyAccessory(item.id));

    row.appendChild(meta);
    row.appendChild(btn);
    accessoryList.appendChild(row);
  });
}

function renderCaseInfo(costXp, rewards) {
  caseCostXp = costXp || 20000;
  caseRewards = rewards || [];
  openCaseBtn.textContent = `Купить кейс за ${caseCostXp.toLocaleString("ru-RU")} XP`;
  if (caseRewards.length > 0) {
    casePreview.textContent = `Награды: ${caseRewards.map((r) => r.label).join(", ")}`;
  }
}

async function loadGameState() {
  if (isLoadingState) {
    return;
  }
  const telegramId = getTelegramId();
  if (!telegramId) {
    return;
  }
  isLoadingState = true;
  try {
    const resp = await fetch(`/api/game/state?telegram_id=${encodeURIComponent(telegramId)}`);
    const data = await parseApiResponse(resp);
    if (!resp.ok || !data.ok) {
      return;
    }
    renderState(data.state);
    renderAccessories(data.accessories);
    renderCaseInfo(data.case_cost_xp, data.case_rewards);
  } catch {
    // keep UI responsive even if backend temporarily unavailable
  } finally {
    isLoadingState = false;
  }
}

function startBalanceAutoRefresh() {
  stopBalanceAutoRefresh();
  balanceRefreshTimer = setInterval(() => {
    loadGameState();
  }, 15000);
}

function stopBalanceAutoRefresh() {
  if (balanceRefreshTimer) {
    clearInterval(balanceRefreshTimer);
    balanceRefreshTimer = null;
  }
}

async function doAction(action) {
  const telegramId = getTelegramId();
  if (!telegramId) {
    gameStatus.textContent = "Запусти мини-апп внутри Telegram.";
    return;
  }
  gameStatus.textContent = "Обновляю...";

  try {
    const resp = await fetch("/api/game/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ telegram_id: telegramId, action }),
    });
    const data = await parseApiResponse(resp);
    if (!resp.ok || !data.ok) {
      throw new Error(data.error || "action failed");
    }
    renderState(data.state);
    renderAccessories(data.accessories);
    renderCaseInfo(data.case_cost_xp, caseRewards);
    gameStatus.textContent = "Готово: начислен XP за уход за питомцем.";
    await loadGameState();
  } catch (error) {
    gameStatus.textContent = `Ошибка: ${error.message || "не удалось выполнить действие"}`;
  }
}

async function buyAccessory(accessoryId) {
  const telegramId = getTelegramId();
  if (!telegramId) {
    gameStatus.textContent = "Запусти мини-апп внутри Telegram.";
    return;
  }
  gameStatus.textContent = "Покупка...";

  try {
    const resp = await fetch("/api/game/buy-accessory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ telegram_id: telegramId, accessory_id: accessoryId }),
    });
    const data = await parseApiResponse(resp);
    if (!resp.ok || !data.ok) {
      throw new Error(data.error || "buy failed");
    }
    renderState(data.state);
    renderAccessories(data.accessories);
    gameStatus.textContent = "Аксессуар куплен, пассивный XP увеличен.";
    await loadGameState();
  } catch (error) {
    gameStatus.textContent = `Ошибка покупки: ${error.message || "не удалось купить"}`;
  }
}

async function openCase() {
  const telegramId = getTelegramId();
  if (!telegramId) {
    gameStatus.textContent = "Запусти мини-апп внутри Telegram.";
    return;
  }
  caseResult.textContent = "Крутим кейс...";

  if (caseRewards.length > 0) {
    for (let i = 0; i < 10; i += 1) {
      const rnd = caseRewards[Math.floor(Math.random() * caseRewards.length)];
      caseResult.textContent = `Крутим кейс... ${rnd.label}`;
      // eslint-disable-next-line no-await-in-loop
      await new Promise((resolve) => setTimeout(resolve, 90));
    }
  }

  try {
    const resp = await fetch("/api/game/open-case", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ telegram_id: telegramId }),
    });
    const data = await parseApiResponse(resp);
    if (!resp.ok || !data.ok) {
      throw new Error(data.error || "case failed");
    }
    renderState(data.state);
    renderAccessories(data.accessories);
    renderCaseInfo(data.case_cost_xp, caseRewards);
    caseResult.textContent = `Выпало: ${data.reward.label}`;
    gameStatus.textContent = "Кейс открыт.";
    await loadGameState();
  } catch (error) {
    caseResult.textContent = "";
    gameStatus.textContent = `Ошибка кейса: ${error.message || "не удалось открыть"}`;
  }
}

connectBtn.addEventListener("click", connectPhantom);
devLoginBtn.addEventListener("click", loginWithoutPhantom);
disconnectBtn.addEventListener("click", disconnectPhantom);
actionButtons.forEach((btn) => {
  btn.addEventListener("click", () => doAction(btn.dataset.action));
});
openCaseBtn.addEventListener("click", openCase);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && menuScreen.classList.contains("active")) {
    loadGameState();
  }
});

const saved = localStorage.getItem("walletAddress");
if (saved) {
  showMenu(saved);
}
restoreSessionFromServer();
