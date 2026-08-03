import { useCallback, useEffect, useState } from "react";

export function useResource<T>(load: (signal: AbortSignal) => Promise<T>, dependencies: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => {
    setError(null);
    setLoading(true);
    setRevision(value => value + 1);
  }, []);

  // A dependency change can cross an authorization boundary (for example, public
  // sample data -> a private signed-in route). Never render data from the previous
  // scope while the new request is in flight.
  useEffect(() => {
    setData(null);
    setError(null);
  // The caller owns the dependency list, matching the load effect below.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    load(controller.signal).then(value => { setData(value); setError(null); }).catch(reason => {
      if (reason instanceof Error && reason.name !== "AbortError") setError(reason);
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
    // The caller controls dependencies; load is intentionally not included.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, revision]);

  return { data, error, loading, refresh };
}
