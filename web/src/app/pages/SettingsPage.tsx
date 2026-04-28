import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { apiClient } from "@/app/apiClient";
import {
  BadgeCheck,
  Bell,
  Bot,
  CheckSquare,
  ChevronRight,
  Clock,
  Globe,
  HardDrive,
  LoaderCircle,
  Lock,
  MessageSquare,
  Moon,
  Shield,
  Sliders,
  Smartphone,
  Sun,
  User,
  Volume2,
  Wifi,
  XCircle,
} from "lucide-react";

type TabId = "profile" | "integrations" | "preferences";
type TelegramStep = "phone" | "code";
type ReasoningEffort = "low" | "medium" | "high" | "xhigh";
type SandboxMode = "read-only" | "workspace-write" | "danger-full-access";

type TelegramAuthStatusResponse = {
  authorized: boolean;
  user: {
    id: number;
    username: string | null;
    phone: string | null;
    first_name: string | null;
    last_name: string | null;
  } | null;
};

type TelegramAuthCodeResponse = {
  phone: string;
  phone_code_hash: string;
  type: string;
  timeout: number | null;
};

type CodexStatusResponse = {
  available: boolean;
  version: string | null;
  auth_present: boolean;
  default_model: string;
  models: string[];
  reasoning_efforts: ReasoningEffort[];
  default_reasoning_effort: ReasoningEffort;
  sandbox_modes: SandboxMode[];
  error: string | null;
};

const TABS: { id: TabId; label: string; icon: React.ComponentType<{ size?: number; className?: string }> }[] = [
  { id: "profile", label: "Профиль", icon: User },
  { id: "integrations", label: "Интеграции", icon: Smartphone },
  { id: "preferences", label: "Предпочтения", icon: Sliders },
];

const TIME_ZONE_OPTIONS = [
  { value: "Europe/Moscow", label: "Москва", offset: "UTC+03:00" },
  { value: "Europe/Kaliningrad", label: "Калининград", offset: "UTC+02:00" },
  { value: "Europe/London", label: "Лондон", offset: "UTC+00:00" },
  { value: "Europe/Berlin", label: "Берлин", offset: "UTC+01:00" },
  { value: "Asia/Dubai", label: "Дубай", offset: "UTC+04:00" },
  { value: "Asia/Yekaterinburg", label: "Екатеринбург", offset: "UTC+05:00" },
  { value: "Asia/Novosibirsk", label: "Новосибирск", offset: "UTC+07:00" },
  { value: "Asia/Vladivostok", label: "Владивосток", offset: "UTC+10:00" },
  { value: "America/New_York", label: "Нью-Йорк", offset: "UTC-05:00" },
  { value: "America/Los_Angeles", label: "Лос-Анджелес", offset: "UTC-08:00" },
];

const STORAGE_BREAKDOWN = [
  { label: "Документы", size: "28.4 ГБ", pct: 44, color: "#4B78F5" },
  { label: "Изображения", size: "18.2 ГБ", pct: 28, color: "#00B894" },
  { label: "Видео", size: "12.1 ГБ", pct: 19, color: "#6C5CE7" },
  { label: "Прочее", size: "5.3 ГБ", pct: 9, color: "#FD9644" },
];

const MODEL_STORAGE_KEY = "stoic.assistant.defaultModel";
const EFFORT_STORAGE_KEY = "stoic.assistant.reasoningEffort";
const SANDBOX_STORAGE_KEY = "stoic.assistant.sandbox";

function detectBrowserTimeZone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Moscow";
}

async function readError(response: Response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    return JSON.stringify(body.detail ?? body);
  } catch {
    return response.statusText || "Ошибка запроса";
  }
}

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <section
      className={`rounded-2xl border border-gray-100 bg-white p-5 ${className}`}
      style={{ boxShadow: "0 1px 4px rgba(0,0,0,0.05)" }}
    >
      {children}
    </section>
  );
}

function Toggle({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      className={`flex h-[22px] min-w-10 items-center rounded-full px-0.5 transition-all ${
        on ? "bg-[#4B78F5]" : "bg-gray-200"
      }`}
    >
      <div
        className="h-4 w-4 rounded-full bg-white shadow-sm transition-transform"
        style={{ transform: on ? "translateX(18px)" : "translateX(0)" }}
      />
    </button>
  );
}

function SectionTitle({
  icon: Icon,
  title,
  subtitle,
}: {
  icon: React.ComponentType<{ size?: number; className?: string }>;
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="mb-4 flex items-start gap-3">
      <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-xl bg-[#EEF3FE]">
        <Icon size={16} className="text-[#4B78F5]" />
      </div>
      <div>
        <h3 className="text-[14px] font-semibold text-gray-800">{title}</h3>
        {subtitle ? <p className="mt-0.5 text-[11.5px] text-gray-400">{subtitle}</p> : null}
      </div>
    </div>
  );
}

function ProfileTab() {
  const [timeZone, setTimeZone] = useState(detectBrowserTimeZone);
  const [autoTimeZone, setAutoTimeZone] = useState(true);
  const selectedTimeZone = TIME_ZONE_OPTIONS.find((tz) => tz.value === timeZone);

  const detectTimeZone = () => {
    setTimeZone(detectBrowserTimeZone());
    setAutoTimeZone(true);
  };

  return (
    <div className="max-w-3xl space-y-5 p-5">
      <Card>
        <SectionTitle icon={Globe} title="Общие настройки" subtitle="Локальные параметры интерфейса и времени." />
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <Clock size={16} className="shrink-0 text-gray-400" />
            <div className="min-w-0 flex-1">
              <p className="text-[13px] font-medium text-gray-800">Часовой пояс</p>
              <p className="text-[11px] text-gray-400">
                {selectedTimeZone ? `${selectedTimeZone.label} · ${selectedTimeZone.offset}` : timeZone}
              </p>
            </div>
            <Toggle on={autoTimeZone} onToggle={detectTimeZone} />
          </div>

          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-[1fr_auto]">
            <select
              value={timeZone}
              onChange={(event) => {
                setTimeZone(event.target.value);
                setAutoTimeZone(false);
              }}
              className="w-full rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5 text-[12.5px] text-gray-700 outline-none focus:border-blue-300"
            >
              {!selectedTimeZone ? <option value={timeZone}>{timeZone}</option> : null}
              {TIME_ZONE_OPTIONS.map((tz) => (
                <option key={tz.value} value={tz.value}>
                  {tz.label} ({tz.offset})
                </option>
              ))}
            </select>
            <button
              onClick={detectTimeZone}
              className="rounded-xl bg-[#EEF3FE] px-3.5 py-2.5 text-[12.5px] font-medium text-[#4B78F5] transition-colors hover:bg-blue-100"
            >
              Определить автоматически
            </button>
          </div>

          <p className="text-[11px] text-gray-400">
            Автоопределение использует часовой пояс устройства: {detectBrowserTimeZone()}.
          </p>
        </div>
      </Card>

      <Card>
        <SectionTitle icon={Shield} title="Безопасность" subtitle="Системные настройки доступа и контроль сессий." />
        <div className="space-y-3">
          {[
            { icon: Lock, label: "Смена пароля", desc: "Последнее обновление 3 месяца назад" },
            { icon: Shield, label: "Двухфакторная защита", desc: "Для локального профиля пока не настроена" },
            { icon: Smartphone, label: "Активные сессии", desc: "Управление подключенными устройствами" },
          ].map(({ icon: Icon, label, desc }) => (
            <button
              key={label}
              className="flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left transition-colors hover:bg-gray-50"
            >
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-100">
                <Icon size={15} className="text-gray-500" />
              </div>
              <div className="flex-1">
                <p className="text-[13px] font-medium text-gray-800">{label}</p>
                <p className="text-[11px] text-gray-400">{desc}</p>
              </div>
              <ChevronRight size={14} className="text-gray-300" />
            </button>
          ))}
        </div>
      </Card>
    </div>
  );
}

function IntegrationsTab() {
  const [telegramStatus, setTelegramStatus] = useState<TelegramAuthStatusResponse | null>(null);
  const [telegramLoading, setTelegramLoading] = useState(true);
  const [telegramBusy, setTelegramBusy] = useState(false);
  const [telegramError, setTelegramError] = useState<string | null>(null);
  const [telegramSuccess, setTelegramSuccess] = useState<string | null>(null);
  const [telegramStep, setTelegramStep] = useState<TelegramStep>("phone");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [lastCodeRequest, setLastCodeRequest] = useState<TelegramAuthCodeResponse | null>(null);

  const [codexStatus, setCodexStatus] = useState<CodexStatusResponse | null>(null);
  const [codexLoading, setCodexLoading] = useState(true);
  const [defaultModel, setDefaultModel] = useState(() => localStorage.getItem(MODEL_STORAGE_KEY) || "gpt-5.2");
  const [defaultEffort, setDefaultEffort] = useState<ReasoningEffort>(
    () => (localStorage.getItem(EFFORT_STORAGE_KEY) as ReasoningEffort | null) || "medium",
  );
  const [defaultSandbox, setDefaultSandbox] = useState<SandboxMode>(
    () => (localStorage.getItem(SANDBOX_STORAGE_KEY) as SandboxMode | null) || "read-only",
  );

  const loadTelegramStatus = async () => {
    try {
      setTelegramLoading(true);
      const response = await apiClient("/api/v1/messages/telegram/auth/status");
      if (!response.ok) throw new Error(await readError(response));
      const body = (await response.json()) as TelegramAuthStatusResponse;
      setTelegramStatus(body);
      if (body.authorized) {
        setTelegramStep("phone");
        setCode("");
        setPassword("");
      }
    } catch (error) {
      setTelegramError(error instanceof Error ? error.message : "Не удалось получить статус Telegram.");
    } finally {
      setTelegramLoading(false);
    }
  };

  const loadCodexStatus = async () => {
    try {
      setCodexLoading(true);
      const response = await apiClient("/api/v1/codex/status");
      if (!response.ok) throw new Error(await readError(response));
      const body = (await response.json()) as CodexStatusResponse;
      setCodexStatus(body);
      setDefaultModel(localStorage.getItem(MODEL_STORAGE_KEY) || body.default_model || "gpt-5.2");
      setDefaultEffort(
        (localStorage.getItem(EFFORT_STORAGE_KEY) as ReasoningEffort | null) || body.default_reasoning_effort,
      );
      setDefaultSandbox(
        (localStorage.getItem(SANDBOX_STORAGE_KEY) as SandboxMode | null) || body.sandbox_modes[0] || "read-only",
      );
    } catch {
      setCodexStatus(null);
    } finally {
      setCodexLoading(false);
    }
  };

  useEffect(() => {
    void loadTelegramStatus();
    void loadCodexStatus();
  }, []);

  useEffect(() => {
    localStorage.setItem(MODEL_STORAGE_KEY, defaultModel);
  }, [defaultModel]);

  useEffect(() => {
    localStorage.setItem(EFFORT_STORAGE_KEY, defaultEffort);
  }, [defaultEffort]);

  useEffect(() => {
    localStorage.setItem(SANDBOX_STORAGE_KEY, defaultSandbox);
  }, [defaultSandbox]);

  const telegramDisplayName = useMemo(() => {
    if (!telegramStatus?.user) return null;
    const fullName = [telegramStatus.user.first_name, telegramStatus.user.last_name].filter(Boolean).join(" ").trim();
    return fullName || telegramStatus.user.username || telegramStatus.user.phone || null;
  }, [telegramStatus]);

  const requestTelegramCode = async () => {
    const normalizedPhone = phone.trim();
    if (!normalizedPhone || telegramBusy) return;

    setTelegramBusy(true);
    setTelegramError(null);
    setTelegramSuccess(null);
    try {
      const response = await apiClient("/api/v1/messages/telegram/auth/request-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone: normalizedPhone }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const body = (await response.json()) as TelegramAuthCodeResponse;
      setLastCodeRequest(body);
      setTelegramStep("code");
      setTelegramSuccess("Код отправлен. Введите его из Telegram.");
    } catch (error) {
      setTelegramError(error instanceof Error ? error.message : "Не удалось запросить код.");
    } finally {
      setTelegramBusy(false);
    }
  };

  const confirmTelegramCode = async () => {
    if (!phone.trim() || !code.trim() || telegramBusy) return;
    if (!lastCodeRequest?.phone_code_hash) {
      setTelegramError("Сессия кода устарела. Запросите код ещё раз.");
      return;
    }

    setTelegramBusy(true);
    setTelegramError(null);
    setTelegramSuccess(null);
    try {
      const response = await apiClient("/api/v1/messages/telegram/auth/sign-in", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          phone: phone.trim(),
          code: code.trim(),
          phone_code_hash: lastCodeRequest?.phone_code_hash,
          password: password.trim() || null,
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      setTelegramSuccess("Telegram подключен.");
      setCode("");
      setPassword("");
      setLastCodeRequest(null);
      await loadTelegramStatus();
    } catch (error) {
      setTelegramError(error instanceof Error ? error.message : "Не удалось завершить авторизацию.");
    } finally {
      setTelegramBusy(false);
    }
  };

  const resetTelegramFlow = () => {
    setTelegramStep("phone");
    setCode("");
    setPassword("");
    setLastCodeRequest(null);
    setTelegramError(null);
    setTelegramSuccess(null);
  };

  return (
    <div className="max-w-3xl space-y-5 p-5">
      <Card>
        <SectionTitle
          icon={Smartphone}
          title="Telegram"
          subtitle="Подключение Telegram ingest через телефон, код и 2FA при необходимости."
        />

        <div className="rounded-2xl border border-[#E8EEF9] bg-[#F8FAFE] p-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex-1">
              <p className="text-[13px] font-semibold text-[#253657]">
                {telegramLoading ? "Проверяем подключение..." : telegramStatus?.authorized ? "Telegram подключен" : "Telegram не подключен"}
              </p>
              <p className="mt-1 text-[11.5px] text-[#7383A7]">
                {telegramStatus?.authorized
                  ? `Аккаунт: ${telegramDisplayName ?? "без имени"}`
                  : "После подключения Stoic получит только список контактов и групп. Старая история сообщений не импортируется."}
              </p>
            </div>
            <div
              className={`inline-flex items-center gap-1 rounded-full px-3 py-1 text-[11px] font-semibold ${
                telegramStatus?.authorized ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"
              }`}
            >
              {telegramStatus?.authorized ? <BadgeCheck size={12} /> : <XCircle size={12} />}
              {telegramStatus?.authorized ? "подключено" : "требует вход"}
            </div>
          </div>
        </div>

        {!telegramStatus?.authorized ? (
          <div className="mt-4 space-y-4">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-[1.2fr_0.8fr]">
              <div className="space-y-2">
                <label className="text-[12px] font-medium text-gray-500">Номер телефона</label>
                <input
                  value={phone}
                  onChange={(event) => setPhone(event.target.value)}
                  placeholder="+79991234567"
                  className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-[13px] text-gray-700 outline-none focus:border-blue-300"
                />
              </div>
              <div className="space-y-2">
                <label className="text-[12px] font-medium text-gray-500">Состояние</label>
                <div className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5 text-[12.5px] text-gray-600">
                  {telegramStep === "phone" ? "Шаг 1: запросить код" : "Шаг 2: подтвердить вход"}
                </div>
              </div>
            </div>

            {telegramStep === "code" ? (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <label className="text-[12px] font-medium text-gray-500">Код из Telegram</label>
                  <input
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    placeholder="12345"
                    className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-[13px] text-gray-700 outline-none focus:border-blue-300"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-[12px] font-medium text-gray-500">Пароль 2FA</label>
                  <input
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="Если включен в Telegram"
                    className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-[13px] text-gray-700 outline-none focus:border-blue-300"
                  />
                </div>
              </div>
            ) : null}

            {lastCodeRequest ? (
              <div className="rounded-xl border border-[#E4EBFB] bg-[#F9FBFF] px-3 py-2.5 text-[12px] text-[#62759D]">
                Код запрошен для {lastCodeRequest.phone}. Тип: {lastCodeRequest.type}
                {lastCodeRequest.timeout ? `, повторный запрос через ${lastCodeRequest.timeout} сек.` : ""}
              </div>
            ) : null}

            <div className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5 text-[12px] text-gray-600">
              После успешного входа Stoic запоминает момент подключения и синхронизирует только новые сообщения, которые появятся позже.
            </div>

            {telegramError ? (
              <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-[12px] text-red-600">{telegramError}</div>
            ) : null}

            {telegramSuccess ? (
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-[12px] text-emerald-700">
                {telegramSuccess}
              </div>
            ) : null}

            <div className="flex flex-wrap gap-2.5">
              {telegramStep === "phone" ? (
                <button
                  onClick={() => void requestTelegramCode()}
                  disabled={!phone.trim() || telegramBusy}
                  className="inline-flex items-center gap-2 rounded-xl bg-[#4B78F5] px-4 py-2.5 text-[12.5px] font-semibold text-white transition-colors hover:bg-[#3f68da] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {telegramBusy ? <LoaderCircle size={14} className="animate-spin" /> : null}
                  Получить код
                </button>
              ) : (
                <>
                  <button
                    onClick={() => void confirmTelegramCode()}
                    disabled={!phone.trim() || !code.trim() || !lastCodeRequest?.phone_code_hash || telegramBusy}
                    className="inline-flex items-center gap-2 rounded-xl bg-[#4B78F5] px-4 py-2.5 text-[12.5px] font-semibold text-white transition-colors hover:bg-[#3f68da] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {telegramBusy ? <LoaderCircle size={14} className="animate-spin" /> : null}
                    Подтвердить вход
                  </button>
                  <button
                    onClick={resetTelegramFlow}
                    disabled={telegramBusy}
                    className="rounded-xl border border-gray-200 px-4 py-2.5 text-[12.5px] font-medium text-gray-600 transition-colors hover:bg-gray-50"
                  >
                    Назад
                  </button>
                </>
              )}

              <button
                onClick={() => void loadTelegramStatus()}
                disabled={telegramBusy || telegramLoading}
                className="rounded-xl border border-gray-200 px-4 py-2.5 text-[12.5px] font-medium text-gray-600 transition-colors hover:bg-gray-50"
              >
                Обновить статус
              </button>
            </div>
          </div>
        ) : (
          <div className="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
            <p className="text-[13px] font-semibold text-emerald-800">Вход завершен</p>
            <p className="mt-1 text-[12px] text-emerald-700">
              Теперь Stoic может читать контакты и группы. В импорт попадут только новые сообщения после момента подключения.
            </p>
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle
          icon={Bot}
          title="Ассистент"
          subtitle="Дефолтные параметры для чатов без явного выбора на экране."
        />

        {codexLoading ? (
          <div className="flex items-center gap-2 text-[12.5px] text-gray-500">
            <LoaderCircle size={14} className="animate-spin" />
            Загружаем параметры Codex...
          </div>
        ) : codexStatus ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`inline-flex items-center gap-1 rounded-full px-3 py-1 text-[11px] font-semibold ${
                  codexStatus.available && codexStatus.auth_present
                    ? "bg-emerald-100 text-emerald-700"
                    : "bg-amber-100 text-amber-700"
                }`}
              >
                {codexStatus.available && codexStatus.auth_present ? <BadgeCheck size={12} /> : <XCircle size={12} />}
                {codexStatus.available && codexStatus.auth_present ? "готов к работе" : "нужна проверка"}
              </span>
              <span className="text-[12px] text-gray-400">
                {codexStatus.version ? `CLI ${codexStatus.version}` : codexStatus.error || "Версия недоступна"}
              </span>
            </div>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <div className="space-y-2">
                <label className="text-[12px] font-medium text-gray-500">Модель по умолчанию</label>
                <select
                  value={defaultModel}
                  onChange={(event) => setDefaultModel(event.target.value)}
                  className="w-full rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5 text-[12.5px] text-gray-700 outline-none focus:border-blue-300"
                >
                  {codexStatus.models.map((model) => (
                    <option key={model} value={model}>
                      {model}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <label className="text-[12px] font-medium text-gray-500">Глубина reasoning</label>
                <select
                  value={defaultEffort}
                  onChange={(event) => setDefaultEffort(event.target.value as ReasoningEffort)}
                  className="w-full rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5 text-[12.5px] text-gray-700 outline-none focus:border-blue-300"
                >
                  {codexStatus.reasoning_efforts.map((effort) => (
                    <option key={effort} value={effort}>
                      {effort}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <label className="text-[12px] font-medium text-gray-500">Sandbox по умолчанию</label>
                <select
                  value={defaultSandbox}
                  onChange={(event) => setDefaultSandbox(event.target.value as SandboxMode)}
                  className="w-full rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5 text-[12.5px] text-gray-700 outline-none focus:border-blue-300"
                >
                  {codexStatus.sandbox_modes.map((mode) => (
                    <option key={mode} value={mode}>
                      {mode}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <p className="text-[11.5px] text-gray-400">
              Значения сохраняются локально в браузере и могут использоваться страницами ассистента как дефолтные.
            </p>
          </div>
        ) : (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-[12px] text-amber-700">
            Не удалось загрузить статус Codex.
          </div>
        )}
      </Card>
    </div>
  );
}

function PreferencesTab() {
  const [theme, setTheme] = useState<"light" | "dark" | "system">("light");
  const [lang, setLang] = useState("ru");
  const [accentColor, setAccentColor] = useState("#4B78F5");
  const [masterNotif, setMasterNotif] = useState(true);
  const [dnd, setDnd] = useState(false);
  const [dndFrom, setDndFrom] = useState("22:00");
  const [dndTo, setDndTo] = useState("09:00");
  const [sound, setSound] = useState(true);
  const [preview, setPreview] = useState(true);
  const [msgNotifs, setMsgNotifs] = useState({
    Telegram: true,
    WhatsApp: true,
    Email: true,
    VK: false,
    Slack: true,
  });
  const [taskLeadTime, setTaskLeadTime] = useState("15");
  const [calLeadTime, setCalLeadTime] = useState("10");
  const [taskNotif, setTaskNotif] = useState(true);
  const [calNotif, setCalNotif] = useState(true);

  const colors = ["#4B78F5", "#6C5CE7", "#00B894", "#FD9644", "#E17055", "#0984E3", "#A29BFE"];

  return (
    <div className="max-w-3xl space-y-5 p-5">
      <Card>
        <SectionTitle icon={Sun} title="Внешний вид" subtitle="Тема, язык и акцентные цвета." />
        <div className="space-y-4">
          <div>
            <label className="mb-2 block text-[12px] font-medium text-gray-500">Тема</label>
            <div className="flex gap-2.5">
              {([
                ["light", "Светлая", Sun],
                ["dark", "Тёмная", Moon],
                ["system", "Системная", Sliders],
              ] as const).map(([id, label, Icon]) => (
                <button
                  key={id}
                  onClick={() => setTheme(id)}
                  className={`flex flex-1 flex-col items-center gap-1.5 rounded-xl border-2 py-3 transition-all ${
                    theme === id ? "border-[#4B78F5] bg-[#EEF3FE]" : "border-gray-100 hover:border-gray-200"
                  }`}
                >
                  <Icon size={18} className={theme === id ? "text-[#4B78F5]" : "text-gray-400"} />
                  <span className={`text-[12px] font-medium ${theme === id ? "text-[#4B78F5]" : "text-gray-500"}`}>
                    {label}
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="mb-2 block text-[12px] font-medium text-gray-500">Акцентный цвет</label>
            <div className="flex gap-2.5">
              {colors.map((color) => (
                <button
                  key={color}
                  onClick={() => setAccentColor(color)}
                  className="h-8 w-8 rounded-full transition-transform hover:scale-110"
                  style={{
                    background: color,
                    outline: accentColor === color ? `3px solid ${color}` : "none",
                    outlineOffset: "2px",
                  }}
                />
              ))}
            </div>
          </div>

          <div>
            <label className="mb-2 block text-[12px] font-medium text-gray-500">Язык</label>
            <div className="flex gap-2">
              {[
                ["ru", "Русский"],
                ["en", "English"],
              ].map(([code, label]) => (
                <button
                  key={code}
                  onClick={() => setLang(code)}
                  className={`rounded-xl border-2 px-3.5 py-2 text-[12.5px] transition-all ${
                    lang === code
                      ? "border-[#4B78F5] bg-[#EEF3FE] font-medium text-[#4B78F5]"
                      : "border-gray-100 text-gray-600 hover:border-gray-200"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </Card>

      <Card>
        <SectionTitle icon={Bell} title="Уведомления" subtitle="Сообщения, события и режим тишины." />
        <div className="space-y-5">
          <div className="flex items-center gap-3">
            <Bell size={15} className="text-gray-400" />
            <p className="flex-1 text-[13px] text-gray-700">Включить уведомления</p>
            <Toggle on={masterNotif} onToggle={() => setMasterNotif((value) => !value)} />
          </div>

          <div className={`space-y-5 transition-opacity ${masterNotif ? "opacity-100" : "pointer-events-none opacity-40"}`}>
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <Volume2 size={15} className="text-gray-400" />
                <p className="flex-1 text-[13px] text-gray-700">Звук уведомлений</p>
                <Toggle on={sound} onToggle={() => setSound((value) => !value)} />
              </div>
              <div className="flex items-center gap-3">
                <MessageSquare size={15} className="text-gray-400" />
                <p className="flex-1 text-[13px] text-gray-700">Показывать превью сообщений</p>
                <Toggle on={preview} onToggle={() => setPreview((value) => !value)} />
              </div>
            </div>

            <div className="h-px bg-gray-100" />

            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <Wifi size={15} className="text-gray-400" />
                <p className="flex-1 text-[13px] text-gray-700">Не беспокоить</p>
                <Toggle on={dnd} onToggle={() => setDnd((value) => !value)} />
              </div>
              {dnd ? (
                <div className="flex items-center gap-3 rounded-xl bg-gray-50 px-4 py-3">
                  <Clock size={14} className="text-gray-400" />
                  <span className="text-[12.5px] text-gray-600">С</span>
                  <input
                    type="time"
                    value={dndFrom}
                    onChange={(event) => setDndFrom(event.target.value)}
                    className="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[12.5px] text-gray-700 outline-none"
                  />
                  <span className="text-[12.5px] text-gray-400">до</span>
                  <input
                    type="time"
                    value={dndTo}
                    onChange={(event) => setDndTo(event.target.value)}
                    className="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[12.5px] text-gray-700 outline-none"
                  />
                </div>
              ) : null}
            </div>

            <div className="h-px bg-gray-100" />

            <div className="space-y-3">
              {(Object.keys(msgNotifs) as Array<keyof typeof msgNotifs>).map((source) => (
                <div key={source} className="flex items-center gap-3">
                  <MessageSquare size={15} className="text-gray-400" />
                  <p className="flex-1 text-[13px] text-gray-700">{source}</p>
                  <Toggle
                    on={msgNotifs[source]}
                    onToggle={() => setMsgNotifs((state) => ({ ...state, [source]: !state[source] }))}
                  />
                </div>
              ))}
            </div>

            <div className="h-px bg-gray-100" />

            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <CheckSquare size={15} className="text-gray-400" />
                <p className="flex-1 text-[13px] text-gray-700">Напоминания о задачах</p>
                <Toggle on={taskNotif} onToggle={() => setTaskNotif((value) => !value)} />
              </div>
              {taskNotif ? (
                <div className="flex items-center gap-3 rounded-xl bg-gray-50 px-4 py-2.5">
                  <Clock size={13} className="text-gray-400" />
                  <span className="flex-1 text-[12.5px] text-gray-600">За сколько напомнить</span>
                  <select
                    value={taskLeadTime}
                    onChange={(event) => setTaskLeadTime(event.target.value)}
                    className="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[12px] text-gray-700 outline-none"
                  >
                    {["5", "10", "15", "30", "60"].map((value) => (
                      <option key={value} value={value}>
                        {value} мин
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}

              <div className="flex items-center gap-3">
                <CheckSquare size={15} className="text-gray-400" />
                <p className="flex-1 text-[13px] text-gray-700">События календаря</p>
                <Toggle on={calNotif} onToggle={() => setCalNotif((value) => !value)} />
              </div>
              {calNotif ? (
                <div className="flex items-center gap-3 rounded-xl bg-gray-50 px-4 py-2.5">
                  <Clock size={13} className="text-gray-400" />
                  <span className="flex-1 text-[12.5px] text-gray-600">За сколько напомнить</span>
                  <select
                    value={calLeadTime}
                    onChange={(event) => setCalLeadTime(event.target.value)}
                    className="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[12px] text-gray-700 outline-none"
                  >
                    {["5", "10", "15", "30", "60"].map((value) => (
                      <option key={value} value={value}>
                        {value} мин
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </Card>

      <Card>
        <SectionTitle icon={HardDrive} title="Хранилище" subtitle="Текущая разбивка использования." />
        <div className="mb-3 flex items-center justify-between">
          <span className="text-[14px] font-semibold text-gray-800">64 / 100 ГБ</span>
          <span className="text-[12px] font-medium text-[#4B78F5]">64%</span>
        </div>
        <div className="mb-3 h-3 overflow-hidden rounded-full bg-gray-100">
          <div className="h-full rounded-full bg-[linear-gradient(90deg,#4B78F5,#6C5CE7)]" style={{ width: "64%" }} />
        </div>
        <div className="space-y-2">
          {STORAGE_BREAKDOWN.map((item) => (
            <div key={item.label} className="flex items-center gap-3">
              <div className="h-3 w-3 rounded-full" style={{ background: item.color }} />
              <span className="flex-1 text-[12.5px] text-gray-700">{item.label}</span>
              <span className="text-[12px] font-medium" style={{ color: item.color }}>
                {item.size}
              </span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

export function SettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const active = (searchParams.get("tab") as TabId | null) ?? "profile";

  const setTab = (id: TabId) => setSearchParams({ tab: id });

  const render = () => {
    switch (active) {
      case "integrations":
        return <IntegrationsTab />;
      case "preferences":
        return <PreferencesTab />;
      case "profile":
      default:
        return <ProfileTab />;
    }
  };

  return (
    <div className="flex h-full flex-col bg-[#F6F7FB]">
      <div className="shrink-0 border-b border-gray-100 bg-white px-4 sm:px-6">
        <div className="flex gap-0 overflow-x-auto" style={{ scrollbarWidth: "none" }}>
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const isActive = active === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setTab(tab.id)}
                className={`flex shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-5 py-3.5 text-[13px] transition-colors ${
                  isActive
                    ? "border-[#4B78F5] font-medium text-[#4B78F5]"
                    : "border-transparent text-gray-500 hover:text-gray-700"
                }`}
              >
                <Icon size={15} className={isActive ? "text-[#4B78F5]" : "text-gray-400"} />
                {tab.label}
              </button>
            );
          })}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">{render()}</div>
    </div>
  );
}
