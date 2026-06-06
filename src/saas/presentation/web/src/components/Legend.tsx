export default function Legend() {
  return (
    <div className="legend">
      <span className="item"><span className="b cov" /> cobertura parcial (&lt;3 proveedores)</span>
      <span className="item"><span className="b clamp" /> ajustado por suelo/techo</span>
      <span className="item"><span className="b stale" /> dato antiguo (stale)</span>
      <span className="item"><span className="b anom" /> anomalía descartada</span>
      <span className="item"><span className="b inf" /> precio derivado de zona (inferido)</span>
      <span className="item" style={{ color: 'var(--ink-faint)' }}>· clic en una celda para ver la procedencia</span>
    </div>
  )
}
