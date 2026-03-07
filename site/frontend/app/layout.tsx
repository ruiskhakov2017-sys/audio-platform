import "./globals.css";
import type { Metadata, Viewport } from "next";
import type { ReactNode } from 'react';
import { Philosopher, Montserrat } from "next/font/google";
import { GlobalAudioPlayer } from '@/components/player/GlobalAudioPlayer';
import { GlobalPlayerBar } from '@/components/player/GlobalPlayerBar';
import { PaywallModal } from '@/components/ui/PaywallModal';
import { AuthToast } from '@/components/ui/AuthToast';
import { AuthInitializer } from '@/components/auth/AuthInitializer';
import { ToasterSonner } from '@/components/ui/ToasterSonner';
import { Footer } from '@/components/layout/Footer';
import { ClientErrorBoundary } from '@/components/ClientErrorBoundary';

const philosopher = Philosopher({
  subsets: ['cyrillic', 'latin'],
  weight: ['400', '700'],
  variable: '--font-philosopher',
});

const montserrat = Montserrat({
  subsets: ['cyrillic', 'latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-montserrat',
});

export const viewport: Viewport = {
  themeColor: "#00B4D8",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  viewportFit: "cover",
};

export const metadata: Metadata = {
  metadataBase: new URL('https://dirtysecrets.ru'),
  title: "Dirty Secrets",
  description: "Dirty Secrets — коллекция откровенных аудио рассказов и тайных фантазий.",
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "Dirty Secrets",
  },
  icons: {
    icon: "/favicon.ico",
    apple: "/favicon.ico",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="ru">
      <body
        suppressHydrationWarning
        className={`${philosopher.variable} ${montserrat.variable} font-sans antialiased min-h-screen text-white flex flex-col`}
        style={{ backgroundColor: '#000814' }}
      >
        {/* Debug: если в консоли мобилки не видно — скрипт падает до гидрации React */}
        <script dangerouslySetInnerHTML={{ __html: 'console.log("App starting...");' }} />
        <AuthInitializer />
        <div className="flex flex-col flex-1 min-h-screen">
          <main className="flex-grow">
            <ClientErrorBoundary>
              {children}
            </ClientErrorBoundary>
          </main>
          <Footer />
        </div>
        <GlobalAudioPlayer />
        <GlobalPlayerBar />
        <PaywallModal />
        <AuthToast />
        <ToasterSonner />
      </body>
    </html>
  );
}
