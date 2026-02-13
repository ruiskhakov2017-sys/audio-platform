'use client';

import { Header } from '@/components/layout/Header';

export default function PrivacyPage() {
  return (
    <div className="min-h-screen">
      <Header />
      <main className="relative z-10 pt-28 pb-20 px-4 max-w-3xl mx-auto">
        <h1 className="font-heading text-3xl font-bold text-white mb-6">Privacy Policy</h1>
        <p className="text-zinc-500 text-sm mb-8">Последнее обновление: {new Date().toLocaleDateString('ru-RU')}</p>
        <div className="prose prose-invert prose-zinc max-w-none text-zinc-300 space-y-4">
          <p>
            Lorem ipsum dolor sit amet, consectetur adipiscing elit. This Privacy Policy describes how we collect,
            use and protect your personal information when you use our service.
          </p>
          <h2 className="font-heading text-xl text-white mt-8 mb-2">Сбор данных</h2>
          <p>
            We may collect your email address, payment information (processed by third-party providers),
            and usage data necessary to provide and improve the service.
          </p>
          <h2 className="font-heading text-xl text-white mt-8 mb-2">Использование данных</h2>
          <p>
            Your data is used to deliver the service, process payments, and communicate with you.
            We do not sell your personal information to third parties.
          </p>
          <h2 className="font-heading text-xl text-white mt-8 mb-2">Безопасность</h2>
          <p>
            We apply industry-standard measures to protect your data. For questions regarding this policy,
            please contact us through the contact page.
          </p>
        </div>
      </main>
    </div>
  );
}
