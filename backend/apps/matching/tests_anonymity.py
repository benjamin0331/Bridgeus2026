"""Unit tests for apps.matching.services.anonymity.assign_anonymous_ids()."""

from django.test import SimpleTestCase

from apps.matching.services.anonymity import ANONYMOUS_IDS, assign_anonymous_ids


class AssignAnonymousIdsTests(SimpleTestCase):
    def test_same_room_id_gives_same_result(self):
        first = assign_anonymous_ids("room-1", [10, 20])
        second = assign_anonymous_ids("room-1", [10, 20])
        self.assertEqual(first, second)

    def test_result_is_independent_of_input_order(self):
        forward = assign_anonymous_ids("room-1", [10, 20])
        backward = assign_anonymous_ids("room-1", [20, 10])
        self.assertEqual(forward, backward)

    def test_participants_in_the_same_room_get_distinct_ids(self):
        result = assign_anonymous_ids("room-1", [10, 20])
        self.assertEqual(len(set(result.values())), 2)

    def test_all_assigned_ids_come_from_the_fixed_pool(self):
        result = assign_anonymous_ids("room-1", [10, 20])
        for anon_id in result.values():
            self.assertIn(anon_id, ANONYMOUS_IDS)

    def test_different_rooms_can_produce_different_assignments(self):
        results = {
            tuple(assign_anonymous_ids(f"room-{i}", [10, 20]).values())
            for i in range(20)
        }
        # Not asserting every room differs (it's random), just that they
        # aren't all identical across 20 distinct rooms.
        self.assertGreater(len(results), 1)

    def test_rejects_more_participants_than_the_pool_size(self):
        with self.assertRaises(ValueError):
            assign_anonymous_ids("room-1", list(range(len(ANONYMOUS_IDS) + 1)))
