"use client";

import { Suspense, useCallback, useState, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { login, fetchAuthStatus, logout, checkIsFirstUser } from "@/lib/auth";
import type { AuthStatus } from "@/lib/auth";
import {
  inheritLoginHash,
  normalizeInternalReturnPath,
} from "@/shared/auth/return-url";

function LoginPageContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = normalizeInternalReturnPath(searchParams.get("next"));
  const resolvedNext = useCallback(
    () =>
      inheritLoginHash(
        next,
        typeof window === "undefined" ? "" : window.location.hash,
      ),
    [next],
  );

  const registered = searchParams.get("registered") === "1";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [current, setCurrent] = useState<AuthStatus | null>(null);
  const [switching, setSwitching] = useState(false);

  useEffect(() => {
    fetchAuthStatus().then((status) => {
      if (status?.authenticated) {
        // Show "already signed in" instead of silently bouncing, so a
        // different role can sign in without hunting for the sidebar icon.
        setCurrent(status);
        return;
      }
      // No users registered yet — send straight to the registration page
      checkIsFirstUser().then((first) => {
        if (first) router.replace("/register");
      });
    });
  }, [router, resolvedNext]);

  async function handleSwitchAccount() {
    setSwitching(true);
    try {
      await logout();
    } finally {
      setCurrent(null);
      setSwitching(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    const result = await login(username, password);

    if (result.ok) {
      router.replace(resolvedNext());
    } else {
      setError(result.error ?? t("Login failed"));
      setLoading(false);
    }
  }

  return (
    <div className="w-full max-w-sm">
      {/* Logo / Title */}
      <div className="text-center mb-8">
        <h1 className="font-serif text-2xl font-semibold text-[var(--foreground)] tracking-tight">
          DeepTutor
        </h1>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          {t("Sign in to your account")}
        </p>
      </div>

      {/* Already signed in — offer to continue or switch account */}
      {current?.authenticated && (
        <div className="mb-4 rounded-lg border border-[var(--border)] bg-[var(--card)] px-4 py-3 text-sm">
          <p className="text-[var(--foreground)]">
            {t("Already signed in as")}
            {" "}
            <span className="font-medium">{current.username}</span>
            {current.role ? (
              <span className="text-[var(--muted-foreground)]">（{current.role}）</span>
            ) : null}
          </p>
          <div className="mt-2.5 flex gap-2">
            <button
              onClick={() => router.replace(resolvedNext())}
              className="rounded-lg px-3 py-1.5 text-sm font-medium bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition-opacity"
            >
              {t("Continue")}
            </button>
            <button
              onClick={() => void handleSwitchAccount()}
              disabled={switching}
              className="rounded-lg px-3 py-1.5 text-sm font-medium border border-[var(--border)] text-[var(--foreground)] hover:bg-[var(--background)]/60 disabled:opacity-50 transition-opacity"
            >
              {switching ? t("Signing out…") : t("Switch account")}
            </button>
          </div>
        </div>
      )}

      {/* Registered success notice */}
      {registered && (
        <div className="mb-4 rounded-lg border border-green-500/30 bg-green-500/10 px-4 py-3 text-sm text-green-600 dark:text-green-400">
          {t("Account created! Sign in to continue.")}
        </div>
      )}

      {/* Card */}
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-2xl shadow-sm px-8 py-8">
        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Email or username */}
          <div>
            <label
              htmlFor="username"
              className="block text-sm font-medium text-[var(--foreground)] mb-1.5"
            >
              {t("Email or username")}
            </label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3.5 py-2.5 rounded-lg border border-[var(--border)]
                         bg-[var(--background)] text-[var(--foreground)]
                         placeholder:text-[var(--muted-foreground)]
                         focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent
                         transition-shadow text-sm"
              placeholder="you@example.com"
            />
          </div>

          {/* Password */}
          <div>
            <label
              htmlFor="password"
              className="block text-sm font-medium text-[var(--foreground)] mb-1.5"
            >
              {t("Password")}
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3.5 py-2.5 rounded-lg border border-[var(--border)]
                         bg-[var(--background)] text-[var(--foreground)]
                         placeholder:text-[var(--muted-foreground)]
                         focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent
                         transition-shadow text-sm"
              placeholder="••••••••"
            />
          </div>

          {/* Error message */}
          {error && (
            <p className="text-sm text-red-500 bg-red-500/10 rounded-lg px-3 py-2">
              {error}
            </p>
          )}

          {/* Submit */}
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 px-4 rounded-lg font-medium text-sm
                       bg-[var(--primary)] text-[var(--primary-foreground)]
                       hover:opacity-90 active:opacity-80
                       disabled:opacity-50 disabled:cursor-not-allowed
                       transition-opacity"
          >
            {loading ? t("Signing in…") : t("Sign in")}
          </button>
        </form>
      </div>

      <p className="mt-6 text-center text-sm text-[var(--muted-foreground)]">
        {t("Don't have an account?")}{" "}
        <Link
          href="/register"
          className="text-[var(--primary)] hover:underline font-medium"
        >
          {t("Create one")}
        </Link>
      </p>

      <p className="mt-3 text-center text-xs text-[var(--muted-foreground)]">
        DeepTutor · Agent-Native Learning
      </p>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="w-full max-w-sm text-center text-sm text-[var(--muted-foreground)]">
          Loading sign in...
        </div>
      }
    >
      <LoginPageContent />
    </Suspense>
  );
}
