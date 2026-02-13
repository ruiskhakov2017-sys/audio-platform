'use client';

import { Header } from '@/components/layout/Header';

export default function TermsPage() {
  return (
    <div className="min-h-screen">
      <Header />
      <main className="relative z-10 pt-28 pb-20 px-4 max-w-3xl mx-auto">
        <h1 className="font-heading text-3xl font-bold text-white mb-6">Пользовательское соглашение</h1>
        <p className="text-zinc-500 text-sm mb-8">Последнее обновление: {new Date().toLocaleDateString('ru-RU')}</p>
        <div className="prose prose-invert prose-zinc max-w-none text-zinc-300 space-y-4">
          <p>
            Lorem ipsum dolor sit amet, consectetur adipiscing elit. By using this service you agree to the terms set forth below.
            Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.
          </p>
          <h2 className="font-heading text-xl text-white mt-8 mb-2">1. Общие положения</h2>
          <p>
            Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris. Duis aute irure dolor in reprehenderit
            in voluptate velit esse cillum dolore eu fugiat nulla pariatur.
          </p>
          <h2 className="font-heading text-xl text-white mt-8 mb-2">2. Использование сервиса</h2>
          <p>
            Excepteur sint occaecat cupidatat non proident. Пользователь обязуется соблюдать законодательство и не нарушать
            права третьих лиц при использовании контента.
          </p>
          <h2 className="font-heading text-xl text-white mt-8 mb-2">3. Подписка и оплата</h2>
          <p>
            Подписка оформляется на условиях, указанных на странице тарифов. Оплата производится в соответствии
            с выбранным планом. Возврат средств регулируется внутренней политикой сервиса.
          </p>
        </div>
      </main>
    </div>
  );
}
