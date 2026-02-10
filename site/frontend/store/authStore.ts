import { create } from 'zustand';
import type { User } from '@supabase/supabase-js';
import { createClient } from '@/utils/supabase/client';

export type Profile = {
  full_name?: string;
  is_premium?: boolean;
  role?: 'user' | 'admin';
};

type AuthState = {
  user: User | null;
  loading: boolean;
  profile: Profile | null;
  isAuthenticated: boolean;
  toastMessage: string | null;
  error: string | null;
  login: (email: string, password: string) => Promise<{ error: Error | null }>;
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
};

function profileFromUser(user: User | null): Profile | null {
  if (!user) return null;
  const meta = user.user_metadata ?? {};
  return {
    full_name: meta.full_name,
    is_premium: meta.is_premium ?? false,
    role: meta.role ?? 'user',
  };
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
    const supabase = createClient();
    if (!supabase) {
      set({ error: 'Supabase не настроен. Добавьте NEXT_PUBLIC_SUPABASE_URL и NEXT_PUBLIC_SUPABASE_ANON_KEY в .env.local (в папке frontend).' });
      return { error: new Error('Supabase not configured') };
    }
    const { data, error } = await supabase.auth.signInWithPassword({
      email,
      password,
    });
    if (error) {
      set({ error: error.message });
      return { error };
    }
    set({
      user: data.user,
      profile: profileFromUser(data.user),
      isAuthenticated: true,
      error: null,
      toastMessage: `Добро пожаловать, ${profileFromUser(data.user)?.full_name || email.split('@')[0]}`,
    });
    return { error: null };
  },

  register: async (email, password, fullName) => {
    set({ error: null });
    const supabase = createClient();
    if (!supabase) {
      set({ error: 'Supabase не настроен. Добавьте NEXT_PUBLIC_SUPABASE_URL и NEXT_PUBLIC_SUPABASE_ANON_KEY в .env.local (в папке frontend).' });
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
      set({
        user: data.user,
        profile: profileFromUser(data.user),
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
    const supabase = createClient();
    if (supabase) await supabase.auth.signOut();
    set({ user: null, profile: null, isAuthenticated: false, toastMessage: null, error: null });
  },

  initialize: async () => {
    set({ loading: true });
    const supabase = createClient();
    if (!supabase) {
      set({ loading: false });
      return;
    }
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (session?.user) {
      set({
        user: session.user,
        profile: profileFromUser(session.user),
        isAuthenticated: true,
        loading: false,
      });
    } else {
      set({ user: null, profile: null, isAuthenticated: false, loading: false });
    }
    supabase.auth.onAuthStateChange((_event, session) => {
      const user = session?.user ?? null;
      set({
        user,
        profile: profileFromUser(user),
        isAuthenticated: !!user,
      });
    });
  },

  showToast: (message) => set({ toastMessage: message }),
  clearToast: () => set({ toastMessage: null }),
  setError: (error) => set({ error }),
}));
