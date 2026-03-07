import { create } from 'zustand';
import type { User } from '@supabase/supabase-js';
import { createClient } from '@/utils/supabase/client';
import { loginWithDjango, registerWithDjango, fetchMeWithDjango, type MeResponse } from '@/lib/authApi';
import { fetchFavoritesFromApi } from '@/lib/favoritesApi';
import { usePlayerStore } from '@/store/playerStore';
import { useFavoritesStore } from '@/store/favoritesStore';

const AUTH_ACCESS_KEY = 'auth_access_token';
const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';

function useDjangoAuth(): boolean {
  return Boolean(API_BASE);
}

export type Profile = {
  full_name?: string;
  is_premium?: boolean;
  role?: 'user' | 'admin';
  avatar_url?: string | null;
};

type AuthState = {
  user: User | { id: string; email?: string; username?: string } | null;
  loading: boolean;
  profile: Profile | null;
  isAuthenticated: boolean;
  toastMessage: string | null;
  error: string | null;
  login: (email: string, password: string) => Promise<{ error: Error | null }>;
  loginWithOAuth: (provider: 'google') => Promise<{ error: Error | null }>;
  register: (
    email: string,
    password: string,
    fullName: string
  ) => Promise<{ error: Error | null; needsEmailConfirm?: boolean }>;
  logout: () => Promise<void>;
  initialize: () => Promise<void>;
  showToast: (message: string) => void;
  clearToast: () => void;
  setError: (error: string | null) => void;
  setProfileAvatar: (avatarUrl: string | null) => void;
};

function profileFromUser(user: User | null): Profile | null {
  if (!user) return null;
  const meta = (user as User & { user_metadata?: Record<string, unknown> }).user_metadata ?? {};
  return {
    full_name: meta.full_name as string | undefined,
    is_premium: (meta.is_premium as boolean) ?? false,
    role: (meta.role as 'user' | 'admin') ?? 'user',
  };
}

function applyPremiumToPlayer(isPremium: boolean) {
  try {
    usePlayerStore.getState().setPremiumStatus(isPremium);
  } catch {}
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  loading: true,
  profile: null,
  isAuthenticated: false,
  toastMessage: null,
  error: null,

  login: async (email, password) => {
    set({ error: null });
    if (useDjangoAuth()) {
      const result = await loginWithDjango(email, password);
      if ('error' in result) {
        set({ error: result.error });
        return { error: new Error(result.error) };
      }
      if (typeof window !== 'undefined') {
        localStorage.setItem(AUTH_ACCESS_KEY, result.tokens.access);
        localStorage.setItem('auth_refresh_token', result.tokens.refresh);
      }
      const profile: Profile = {
        full_name: result.user.username,
        is_premium: result.user.is_premium,
        role: 'user',
        avatar_url: result.user.avatar_url ?? null,
      };
      applyPremiumToPlayer(result.user.is_premium);
      set({
        user: { id: String(result.user.id), email: result.user.email, username: result.user.username },
        profile,
        isAuthenticated: true,
        error: null,
        toastMessage: `Добро пожаловать, ${result.user.username}`,
      });
      try {
        const favoriteIds = await fetchFavoritesFromApi();
        useFavoritesStore.getState().setLikedIds(favoriteIds.map(String));
      } catch (_) {}
      return { error: null };
    }
    const supabase = createClient();
    if (!supabase) {
      set({ error: 'Supabase не настроен.' });
      return { error: new Error('Supabase not configured') };
    }
    const { data, error } = await supabase.auth.signInWithPassword({
      email,
      password,
    });
    if (error) {
      if (typeof window !== 'undefined') console.error('[Supabase Auth]', error);
      const friendly =
        /email not confirmed|Email not confirmed|confirm your email/i.test(error.message)
          ? 'Подтвердите почту: проверьте письмо от Supabase или папку «Спам».'
          : error.message;
      set({ error: friendly });
      return { error };
    }
    const prof = profileFromUser(data.user);
    applyPremiumToPlayer(prof?.is_premium ?? false);
    set({
      user: data.user,
      profile: prof,
      isAuthenticated: true,
      error: null,
      toastMessage: `Добро пожаловать, ${prof?.full_name || email.split('@')[0]}`,
    });
    return { error: null };
  },

  loginWithOAuth: async (provider) => {
    set({ error: null });
    if (useDjangoAuth()) {
      set({ error: 'OAuth доступен только при входе через Supabase.' });
      return { error: new Error('OAuth not available with Django API') };
    }
    const supabase = createClient();
    if (!supabase) {
      set({ error: 'Supabase не настроен.' });
      return { error: new Error('Supabase not configured') };
    }
    const origin = typeof window !== 'undefined' ? window.location.origin : '';
    const redirectTo = `${origin}/auth/callback`;
    const { data, error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo },
    });
    if (error) {
      if (typeof window !== 'undefined') console.error('[Supabase OAuth]', error);
      set({ error: error.message });
      return { error };
    }
    if (data?.url && typeof window !== 'undefined') {
      window.location.href = data.url;
    }
    return { error: null };
  },

  register: async (email, password, fullName) => {
    set({ error: null });
    if (useDjangoAuth()) {
      const result = await registerWithDjango(email, password);
      if ('error' in result && result.error) {
        set({ error: result.error });
        return { error: new Error(result.error), needsEmailConfirm: false };
      }
      if ('tokens' in result && result.tokens.access) {
        if (typeof window !== 'undefined') {
          localStorage.setItem(AUTH_ACCESS_KEY, result.tokens.access);
          localStorage.setItem('auth_refresh_token', result.tokens.refresh);
        }
        const user = (result as { user: { id: number; email: string; username: string; is_premium: boolean } }).user;
        const profile: Profile = { full_name: fullName || user.username, is_premium: user.is_premium, role: 'user', avatar_url: (result as { user: MeResponse }).user.avatar_url ?? null };
        applyPremiumToPlayer(user.is_premium);
        set({
          user: { id: String(user.id), email: user.email, username: user.username },
          profile,
          isAuthenticated: true,
          error: null,
          toastMessage: `Добро пожаловать, ${fullName || user.username}`,
        });
        try {
          const favoriteIds = await fetchFavoritesFromApi();
          useFavoritesStore.getState().setLikedIds(favoriteIds.map(String));
        } catch (_) {}
      }
      return { error: null, needsEmailConfirm: false };
    }
    const supabase = createClient();
    if (!supabase) {
      set({ error: 'Supabase не настроен.' });
      return { error: new Error('Supabase not configured'), needsEmailConfirm: false };
    }
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: { data: { full_name: fullName } },
    });
    if (error) {
      set({ error: error.message });
      return { error, needsEmailConfirm: false };
    }
    const needsEmailConfirm =
      data.user && !data.session && data.user.identities?.length;
    if (data.session) {
      const prof = profileFromUser(data.user);
      applyPremiumToPlayer(prof?.is_premium ?? false);
      set({
        user: data.user,
        profile: prof,
        isAuthenticated: true,
        error: null,
        toastMessage: `Добро пожаловать, ${fullName}`,
      });
    } else {
      set({ error: null });
    }
    return { error: null, needsEmailConfirm: !!needsEmailConfirm };
  },

  logout: async () => {
    if (useDjangoAuth() && typeof window !== 'undefined') {
      localStorage.removeItem(AUTH_ACCESS_KEY);
      localStorage.removeItem('auth_refresh_token');
    } else {
      const supabase = createClient();
      if (supabase) await supabase.auth.signOut();
    }
    applyPremiumToPlayer(false);
    set({ user: null, profile: null, isAuthenticated: false, toastMessage: null, error: null });
  },

  initialize: async () => {
    set({ loading: true });
    if (useDjangoAuth() && typeof window !== 'undefined') {
      const access = localStorage.getItem(AUTH_ACCESS_KEY);
      if (access) {
        const me = await fetchMeWithDjango(access);
        if (me) {
          applyPremiumToPlayer(me.is_premium);
          set({
            user: { id: String(me.id), email: me.email, username: me.username },
            profile: { full_name: me.username, is_premium: me.is_premium, role: 'user', avatar_url: me.avatar_url ?? null },
            isAuthenticated: true,
            loading: false,
          });
          try {
            const favoriteIds = await fetchFavoritesFromApi();
            useFavoritesStore.getState().setLikedIds(favoriteIds.map(String));
          } catch (_) {}
          set({ loading: false });
          return;
        }
      }
      set({ user: null, profile: null, isAuthenticated: false, loading: false });
      return;
    }
    const supabase = createClient();
    if (!supabase) {
      set({ loading: false });
      return;
    }
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (session?.user) {
      const prof = profileFromUser(session.user);
      applyPremiumToPlayer(prof?.is_premium ?? false);
      set({
        user: session.user,
        profile: prof,
        isAuthenticated: true,
        loading: false,
      });
    } else {
      set({ user: null, profile: null, isAuthenticated: false, loading: false });
    }
    supabase.auth.onAuthStateChange((_event, session) => {
      const user = session?.user ?? null;
      const prof = profileFromUser(user);
      applyPremiumToPlayer(prof?.is_premium ?? false);
      set({
        user,
        profile: prof,
        isAuthenticated: !!user,
      });
    });
  },

  showToast: (message) => set({ toastMessage: message }),
  clearToast: () => set({ toastMessage: null }),
  setError: (error) => set({ error }),
  setProfileAvatar: (avatarUrl) =>
    set((s) => ({
      profile: s.profile ? { ...s.profile, avatar_url: avatarUrl } : null,
    })),
}));
