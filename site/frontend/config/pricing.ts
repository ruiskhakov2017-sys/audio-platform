export type PremiumPlanId = 'tainyi' | 'gryaznyi' | 'oderzhimyi';

export type PremiumPlanConfig = {
  id: PremiumPlanId;
  name: string;
  period: string;
  durationMonths: number;
  description: string;
  priceRub: number;
};

export const PREMIUM_PLANS: Record<PremiumPlanId, PremiumPlanConfig> = {
  tainyi: {
    id: 'tainyi',
    name: 'Тайный',
    period: '1 месяц',
    durationMonths: 1,
    description: 'Базовый доступ',
    priceRub: 490,
  },
  gryaznyi: {
    id: 'gryaznyi',
    name: 'Грязный',
    period: '3 месяца',
    durationMonths: 3,
    description: 'Доступ + HD звук',
    priceRub: 1290,
  },
  oderzhimyi: {
    id: 'oderzhimyi',
    name: 'Одержимый',
    period: '1 год',
    durationMonths: 12,
    description: 'Максимальное качество + скидка 50% на озвучку личных сценариев',
    priceRub: 3490,
  },
};

export const PREMIUM_PLAN_LIST: PremiumPlanConfig[] = [
  PREMIUM_PLANS.tainyi,
  PREMIUM_PLANS.gryaznyi,
  PREMIUM_PLANS.oderzhimyi,
];

export const PREMIUM_STORY_FROM_PRICE_RUB = 530;

export function formatRub(priceRub: number): string {
  return `${priceRub.toLocaleString('ru-RU')} ₽`;
}
