interface PlaceholderProps {
  title: string;
}

export default function Placeholder({ title }: PlaceholderProps) {
  return (
    <section aria-labelledby="placeholder-heading">
      <h1 id="placeholder-heading" className="page-heading">
        {title}
      </h1>
      <p className="page-placeholder">Bu bölüm henüz uygulanmadı.</p>
    </section>
  );
}
