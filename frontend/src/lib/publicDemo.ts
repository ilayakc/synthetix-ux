interface DatedItem {
  created_at: string;
}

/** Public demo ziyaretçisine yalnızca iki amaçlı, en güncel örneği gösterir.
 * Eski yeniden denemeler veritabanında denetim izi olarak kalır; demo ekranında
 * ayrı testlermiş gibi çoğalmaz. Normal kullanıcı hesapları etkilenmez. */
export function selectPublicDemoItems<T extends DatedItem>(items: T[], limit = 2): T[] {
  return [...items]
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    .slice(0, limit);
}
