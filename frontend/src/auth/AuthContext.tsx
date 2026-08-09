import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  ApiError,
  type LoginRequest,
  type RegisterRequest,
  type SessionResponse,
  getMe,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
} from "../api/client";
import { onSessionExpired } from "./sessionEvents";

type AuthStatus = "loading" | "authenticated" | "unauthenticated";

interface AuthContextValue {
  status: AuthStatus;
  session: SessionResponse | null;
  // Oturum bir arka plan istegi (401) tarafindan sonlandirildiginda true
  // olur; giris ekrani "oturumunuzun suresi doldu" mesajini gosterebilir.
  sessionExpired: boolean;
  login: (body: LoginRequest) => Promise<SessionResponse>;
  register: (body: RegisterRequest) => Promise<void>;
  logout: () => Promise<void>;
  clearSessionExpired: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [sessionExpired, setSessionExpired] = useState(false);

  useEffect(() => {
    let cancelled = false;

    getMe()
      .then((data) => {
        if (cancelled) return;
        setSession(data);
        setStatus("authenticated");
      })
      .catch(() => {
        if (cancelled) return;
        setSession(null);
        setStatus("unauthenticated");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // Diger sayfalarin/hook'larin "oturum artik gecersiz" bilgisini merkezi
  // olarak bildirebilmesi icin bir global olay dinlenir (bkz.
  // `notifySessionExpired`, ornegin bir arka plan verisi cekilirken 401
  // alindiginda cagrilir).
  useEffect(() => {
    return onSessionExpired(() => {
      setSession(null);
      setStatus("unauthenticated");
      setSessionExpired(true);
    });
  }, []);

  const login = useCallback(async (body: LoginRequest) => {
    const data = await apiLogin(body);
    setSession(data);
    setStatus("authenticated");
    setSessionExpired(false);
    return data;
  }, []);

  const register = useCallback(async (body: RegisterRequest) => {
    const data = await apiRegister(body);
    setSession(data);
    setStatus("authenticated");
    setSessionExpired(false);
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      // Sunucu tarafinda oturum zaten gecersiz olsa bile istemci tarafinda
      // cikis yapilmis sayilir.
    }
    setSession(null);
    setStatus("unauthenticated");
  }, []);

  const clearSessionExpired = useCallback(() => setSessionExpired(false), []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, session, sessionExpired, login, register, logout, clearSessionExpired }),
    [status, session, sessionExpired, login, register, logout, clearSessionExpired],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth, AuthProvider icinde kullanilmalidir");
  }
  return context;
}

export function isUnauthorizedError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}
