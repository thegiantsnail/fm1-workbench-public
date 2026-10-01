//! The 32 DX7 algorithms: which operators modulate which, and which are heard (carriers).

/// Modulation edges "from>to" and the feedback operator, algorithms 1..32 (workbench `dx7.js`).
const ALGORITHMS: [&str; 32] = [
    "2>1,6>5,5>4,4>3|6",
    "2>1,6>5,5>4,4>3|2",
    "3>2,2>1,6>5,5>4|6",
    "3>2,2>1,6>5,5>4|6",
    "2>1,4>3,6>5|6",
    "2>1,4>3,6>5|6",
    "2>1,4>3,5>3,6>5|6",
    "2>1,4>3,5>3,6>5|4",
    "2>1,4>3,5>3,6>5|2",
    "3>2,2>1,5>4,6>4|3",
    "3>2,2>1,5>4,6>4|6",
    "2>1,4>3,5>3,6>3|2",
    "2>1,4>3,5>3,6>3|6",
    "2>1,4>3,5>4,6>4|6",
    "2>1,4>3,5>4,6>4|2",
    "2>1,3>1,4>3,5>1,6>5|6",
    "2>1,3>1,4>3,5>1,6>5|2",
    "2>1,3>1,4>1,5>4,6>5|3",
    "3>2,2>1,6>4,6>5|6",
    "3>1,3>2,5>4,6>4|3",
    "3>1,3>2,6>4,6>5|3",
    "2>1,6>3,6>4,6>5|6",
    "3>2,6>4,6>5|6",
    "6>3,6>4,6>5|6",
    "6>4,6>5|6",
    "3>2,5>4,6>4|6",
    "3>2,5>4,6>4|3",
    "2>1,5>4,4>3|5",
    "4>3,6>5|6",
    "5>4,4>3|5",
    "6>5|6",
    "|6",
];

fn source(algorithm: u8) -> (&'static str, &'static str) {
    ALGORITHMS[(algorithm as usize).min(31)].split_once('|').unwrap_or(("", "6"))
}

/// (modulator, target) pairs, operators numbered 1..=6. `algorithm` is 0-based as stored.
pub fn edges(algorithm: u8) -> impl Iterator<Item = (u8, u8)> {
    source(algorithm).0.split(',').filter_map(|edge| {
        let (from, to) = edge.split_once('>')?;
        Some((from.parse().ok()?, to.parse().ok()?))
    })
}

/// `carriers(a)[op - 1]` is true when operator `op` is heard directly.
pub fn carriers(algorithm: u8) -> [bool; 6] {
    let mut carrier = [true; 6];
    for (from, _) in edges(algorithm) {
        carrier[from as usize - 1] = false;
    }
    carrier
}

pub fn feedback_op(algorithm: u8) -> u8 {
    source(algorithm).1.parse().unwrap_or(6)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The workbench's `dx7.py` lists carriers independently; both tables must agree.
    const CARRIERS: [&[u8]; 32] = [
        &[1, 3], &[1, 3], &[1, 4], &[1, 4], &[1, 3, 5], &[1, 3, 5], &[1, 3], &[1, 3],
        &[1, 3], &[1, 4], &[1, 4], &[1, 3], &[1, 3], &[1, 3], &[1, 3], &[1],
        &[1], &[1], &[1, 4, 5], &[1, 2, 4], &[1, 2, 4, 5], &[1, 3, 4, 5], &[1, 2, 4, 5],
        &[1, 2, 3, 4, 5], &[1, 2, 3, 4, 5], &[1, 2, 4], &[1, 2, 4], &[1, 3, 6], &[1, 2, 3, 5],
        &[1, 2, 3, 6], &[1, 2, 3, 4, 5], &[1, 2, 3, 4, 5, 6],
    ];

    #[test]
    fn carriers_match_the_workbench_table() {
        for (algorithm, expected) in CARRIERS.iter().enumerate() {
            let found: Vec<u8> = carriers(algorithm as u8)
                .iter()
                .enumerate()
                .filter_map(|(i, &c)| c.then_some(i as u8 + 1))
                .collect();
            assert_eq!(&found, expected, "algorithm {}", algorithm + 1);
        }
        assert_eq!(edges(0).collect::<Vec<_>>(), vec![(2, 1), (6, 5), (5, 4), (4, 3)]);
        assert_eq!(edges(31).count(), 0);
        assert_eq!((feedback_op(1), feedback_op(27), feedback_op(200)), (2, 5, 6));
    }
}
