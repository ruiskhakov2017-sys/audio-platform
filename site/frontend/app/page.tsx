import { Header } from '@/components/layout/Header';
import { Hero } from '@/components/home/Hero';
import { Features } from '@/components/home/Features';
import { AboutSection } from '@/components/home/AboutSection';
import { CategoryChoices } from '@/components/home/CategoryChoices';
import { CustomExperience } from '@/components/home/CustomExperience';
import { TopSales } from '@/components/home/TopSales';
import { Reviews } from '@/components/home/Reviews';
import { FooterCTA } from '@/components/home/FooterCTA';

export default function HomePage() {
  return (
    <div className="min-h-screen">
      <Header />
      <Hero />
      <Features />
      <AboutSection />
      <CategoryChoices />
      <TopSales />
      <CustomExperience />
      <Reviews />
      <FooterCTA />
    </div>
  );
}
