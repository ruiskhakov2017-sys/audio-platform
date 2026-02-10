import Hero from '@/components/home/Hero';
import Features from '@/components/home/Features';
import AboutSection from '@/components/home/AboutSection';
import CategoryChoices from '@/components/home/CategoryChoices';
import CustomExperience from '@/components/home/CustomExperience';
import TopSales from '@/components/home/TopSales';

export default function HomePage() {
  return (
    <div className="min-h-screen bg-[#000814] text-white">
      <Hero />
      <Features />
      <AboutSection />
      <CategoryChoices />
      <CustomExperience />
      <TopSales />
      <footer className="border-t border-white/10 py-8">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 text-sm text-zinc-600 sm:px-8 lg:px-12">
          <span>EroticAudio</span>
          <div className="flex gap-6">
            <a href="/" className="text-zinc-400 no-underline hover:text-[#00B4D8]">
              Контакты
            </a>
            <a href="/" className="text-zinc-400 no-underline hover:text-[#00B4D8]">
              Оферта
            </a>
            <a href="/" className="text-zinc-400 no-underline hover:text-[#00B4D8]">
              Конфиденциальность
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
