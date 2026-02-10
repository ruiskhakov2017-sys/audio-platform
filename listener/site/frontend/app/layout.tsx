import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { Geist, Geist_Mono } from 'next/font/google';
import './globals.css';
import Header from '@/components/layout/Header';
import PlayerBar from '@/components/layout/PlayerBar';
import FullScreen from '@/components/v1/features/player/FullScreen';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

export const metadata: Metadata = {
  title: 'EroticAudio',
  description: 'Аудио-портал. Искусство звука.',
};

const PLAYER_BAR_HEIGHT = 100;

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="ru">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased min-h-screen bg-[#000814] text-white`}
      >
        <Header />
        <main
          className="overflow-y-auto overflow-x-hidden pt-20"
          style={{
            paddingBottom: PLAYER_BAR_HEIGHT,
            minHeight: '100vh',
          }}
        >
          {children}
        </main>
        <PlayerBar />
        <FullScreen />
      </body>
    </html>
  );
}
