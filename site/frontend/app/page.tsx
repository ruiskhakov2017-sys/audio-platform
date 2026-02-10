import { Header } from '@/components/layout/Header';
import { Hero } from '@/components/home/Hero';
import { RecentlyPlayedSection } from '@/components/home/RecentlyPlayedSection';
import { Features } from '@/components/home/Features';
import { AboutSection } from '@/components/home/AboutSection';
import { CategoryChoices } from '@/components/home/CategoryChoices';
import { CustomExperience } from '@/components/home/CustomExperience';
import { TopSales } from '@/components/home/TopSales';
import { Reviews } from '@/components/home/Reviews';
import { Footer } from '@/components/home/Footer';

export default function HomePage() {
  return (
    <div className="min-h-screen">
      <Header />
      <Hero />
      <RecentlyPlayedSection />
      <Features />
      <AboutSection />
      <CategoryChoices />
      <TopSales />
      <CustomExperience />
      <Reviews />
      <Footer />
    </div>
  );
}
