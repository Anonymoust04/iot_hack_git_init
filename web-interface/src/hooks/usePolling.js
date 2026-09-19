import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { REFRESH_MS } from '../services/api'

// Load data now and every REFRESH_MS (VITE_REFRESH_MS in .env), so the page stays live.
// `deps`: load again at once when these change (e.g. the plate in the URL).
// Returns { data, error, refresh }: data is null until the first load finishes;
// call refresh() after an action to update at once.
export function usePolling(load, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const loadRef = useRef(load)

  useLayoutEffect(() => {
    loadRef.current = load
  })

  const refresh = useCallback(() => {
    return loadRef.current()
      .then((result) => {
        setData(result)
        setError(null)
      })
      .catch(setError)
  }, [])

  const depsKey = JSON.stringify(deps)

  useEffect(() => {
    const first = setTimeout(refresh, 0)
    const timer = setInterval(refresh, REFRESH_MS)
    return () => {
      clearTimeout(first)
      clearInterval(timer)
    }
  }, [refresh, depsKey])

  return { data, error, refresh }
}
