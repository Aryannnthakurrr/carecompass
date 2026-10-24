// Delhi NCR reference points: names a GPS fix ("Near Saket") and backs the manual location picker.
export const LOCALITIES = [
  { name: 'Okhla', lat: 28.5402, lng: 77.2700 },
  { name: 'Saket', lat: 28.5245, lng: 77.2066 },
  { name: 'Lajpat Nagar', lat: 28.5677, lng: 77.2433 },
  { name: 'Connaught Place', lat: 28.6315, lng: 77.2167 },
  { name: 'Laxmi Nagar', lat: 28.6304, lng: 77.2777 },
  { name: 'Vasant Kunj', lat: 28.5200, lng: 77.1590 },
  { name: 'Dwarka', lat: 28.5921, lng: 77.0460 },
  { name: 'Rohini', lat: 28.7495, lng: 77.0565 },
  { name: 'Noida Sector 18', lat: 28.5708, lng: 77.3260 },
  { name: 'Gurugram Cyber City', lat: 28.4950, lng: 77.0895 },
];

export function haversineKm(a, b) {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad, dLng = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

export function nearestLocality(p, maxKm = 6) {
  let best = null, bestKm = Infinity;
  for (const l of LOCALITIES) {
    const km = haversineKm(p, l);
    if (km < bestKm) [best, bestKm] = [l, km];
  }
  return bestKm <= maxKm ? best : null;
}
